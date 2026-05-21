from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from backend.config import settings


class LLMClient:
    def __init__(self) -> None:
        self.api_base = settings.openai_api_base.rstrip("/") if settings.openai_api_base else None
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model

    @property
    def enabled(self) -> bool:
        return bool(self.api_base and self.api_key)

    def _fallback_with_trace(
        self,
        fallback: dict[str, Any],
        *,
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        payload = dict(fallback)
        payload["_llm_fallback"] = {
            "reason": reason,
            "message": message[:1000],
            "api_base": self.api_base,
            "model": self.model,
            "timeout_seconds": settings.llm_timeout_seconds,
        }
        return payload

    def _parse_json_content(self, content: str) -> dict[str, Any]:
        stripped = content.strip()
        fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            stripped = fence_match.group(1).strip()
        return json.loads(stripped)

    def _chat_json_with_curl(self, payload: dict[str, Any]) -> dict[str, Any]:
        curl_path = shutil.which("curl.exe") or shutil.which("curl")
        if not curl_path:
            raise RuntimeError("curl executable not found")

        with tempfile.TemporaryDirectory(prefix="mineru_llm_") as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            response_path = Path(temp_dir) / "response.json"
            request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            command = [
                curl_path,
                "-sS",
                "-L",
                "--http1.1",
                "--connect-timeout",
                str(max(5, int(settings.llm_timeout_seconds))),
                "--max-time",
                str(max(10, int(settings.llm_timeout_seconds) + 15)),
                "-H",
                f"Authorization: Bearer {self.api_key}",
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                f"@{request_path}",
                "-o",
                str(response_path),
                f"{self.api_base}/chat/completions",
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.llm_timeout_seconds + 25,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"curl exit {completed.returncode}: {(completed.stderr or completed.stdout)[:1000]}")
            response_payload = json.loads(response_path.read_text(encoding="utf-8"))

        if isinstance(response_payload, dict) and response_payload.get("error"):
            raise RuntimeError(f"curl API error: {json.dumps(response_payload.get('error'), ensure_ascii=False)[:1000]}")

        content = response_payload["choices"][0]["message"]["content"]
        parsed = self._parse_json_content(content)
        if isinstance(parsed, dict):
            parsed["_llm_transport_fallback"] = {
                "from": "httpx",
                "to": "curl",
            }
        return parsed

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            return self._fallback_with_trace(
                fallback,
                reason="not_configured",
                message="未配置 OPENAI_API_BASE 或 OPENAI_API_KEY，已使用本地兜底结果。",
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return self._parse_json_content(content)
        except Exception as exc:
            try:
                return await asyncio.to_thread(self._chat_json_with_curl, payload)
            except Exception as curl_exc:
                message = (
                    f"httpx {exc.__class__.__name__}: {str(exc) or '外部 LLM 调用失败'}; "
                    f"curl {curl_exc.__class__.__name__}: {str(curl_exc) or 'curl 兜底失败'}"
                )
                retry_payload = dict(payload)
                retry_payload.pop("response_format", None)
                try:
                    parsed = await asyncio.to_thread(self._chat_json_with_curl, retry_payload)
                    if isinstance(parsed, dict):
                        parsed["_llm_transport_fallback"] = {
                            "from": "httpx",
                            "to": "curl",
                            "note": "response_format removed for compatibility",
                        }
                    return parsed
                except Exception as retry_exc:
                    message = f"{message}; curl_retry {retry_exc.__class__.__name__}: {str(retry_exc)}"
            return self._fallback_with_trace(
                fallback,
                reason=exc.__class__.__name__,
                message=message or "外部 LLM 调用失败，已使用本地兜底结果。",
            )


llm_client = LLMClient()


async def build_processing_plan(
    filename: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    fallback = {
        "goal": "将文档解析为 Markdown，并生成规范化的结构化 JSON 片段。",
        "steps": [
            "校验输入类型，并创建独立任务工作区。",
            "按所选后端和解析参数调用 MinerU。",
            "收集 Markdown、content_list、middle_json、图片和元数据。",
            "将解析元素规范化为 JSONL 结构化片段。",
            "检查输出完整性，并生成可复现的处理日志。",
        ],
        "risk_controls": [
            "保留 MinerU 原始输出，便于审计和复现。",
            "返回文件路径和元素统计，便于下游校验。",
            "在任务日志中暴露解析警告和异常信息。",
        ],
    }
    prompt = (
        "为一个面向文档结构化处理的数据智能体生成简洁执行计划。"
        "只输出JSON，字段包含goal、steps、risk_controls。所有自然语言内容必须使用中文。"
        f"\n文件名: {filename}\n选项: {json.dumps(options, ensure_ascii=False)}"
    )
    return await llm_client.chat_json(
        "你是生产级数据智能体规划器。只返回严格 JSON，所有自然语言字段必须使用中文。",
        prompt,
        fallback=fallback,
    )


async def build_quality_report(
    manifest: dict[str, Any],
    chunks_preview: list[dict[str, Any]],
) -> dict[str, Any]:
    fallback = {
        "verdict": "通过" if manifest.get("summary", {}).get("documents", 0) else "需复核",
        "checks": [
            {
                "name": "文档数量",
                "status": "通过" if manifest.get("summary", {}).get("documents", 0) else "警告",
                "message": "至少检测到一个已解析文档。"
                if manifest.get("summary", {}).get("documents", 0)
                else "没有检测到已解析文档。",
            },
            {
                "name": "结构片段数量",
                "status": "通过" if manifest.get("summary", {}).get("chunks", 0) else "警告",
                "message": "已生成结构化片段。"
                if manifest.get("summary", {}).get("chunks", 0)
                else "没有生成结构化片段。",
            },
        ],
        "suggestions": [],
    }
    prompt = (
        "请审查文档解析结果的完整性，返回JSON。字段包含verdict、checks、suggestions。"
        "checks中每项包含name、status、message。所有自然语言内容必须使用中文。"
        f"\nmanifest摘要: {json.dumps(manifest.get('summary', {}), ensure_ascii=False)}"
        f"\n文档列表: {json.dumps(manifest.get('documents', []), ensure_ascii=False)[:4000]}"
        f"\nchunks预览: {json.dumps(chunks_preview, ensure_ascii=False)[:4000]}"
    )
    return await llm_client.chat_json(
        "你是严格的数据质量审查员。只返回严格 JSON，所有自然语言字段必须使用中文。",
        prompt,
        fallback=fallback,
    )
