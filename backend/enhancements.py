from __future__ import annotations

import importlib
import json
import base64
import io
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.schemas import JobRecord


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
FINANCE_KEYWORDS = {
    "资产",
    "负债",
    "利润",
    "收入",
    "现金",
    "合计",
    "小计",
    "本期",
    "上期",
    "同比",
    "营收",
    "成本",
    "股东权益",
    "revenue",
    "asset",
    "liability",
    "profit",
    "cash",
    "total",
    "subtotal",
    "equity",
}
REFERENCE_PATTERN = re.compile(
    r"(上述|前述|本公司|该公司|该项目|该表|该图|该方法|该模型|其|this|that|above|aforementioned|the company)",
    re.IGNORECASE,
)
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+(]?\d[\d,]*(?:\.\d+)?%?\)?")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _module_report(
    name: str,
    status: str,
    summary: str,
    *,
    details: dict[str, Any] | None = None,
    artifacts: list[str] | None = None,
    skipped_reason: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "summary": summary,
        "details": details or {},
        "artifacts": artifacts or [],
        "tool_calls": tool_calls or [],
        "warnings": warnings or [],
    }
    if skipped_reason:
        payload["skipped_reason"] = skipped_reason
    return payload


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return str(path)


def _optional_import(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _chunk_text(chunk: dict[str, Any]) -> str:
    text = chunk.get("text")
    if isinstance(text, str):
        return text
    raw = chunk.get("raw")
    if isinstance(raw, dict) and isinstance(raw.get("text"), str):
        return raw["text"]
    return ""


def _read_jsonl(path: Path, limit: int = 10000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if len(rows) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _image_preprocess(record: JobRecord) -> tuple[dict[str, Any], str | None]:
    input_path = Path(record.input_path)
    if input_path.suffix.lower() not in IMAGE_SUFFIXES:
        return (
            _module_report(
                "image_preprocess",
                "skipped",
                "输入不是图片文件，MinerU 将直接处理原始文档。",
                skipped_reason="仅对图片类输入启用预处理；PDF/Office 文档暂不在此步骤改写输入。",
            ),
            None,
        )

    if not settings.enable_image_preprocess:
        return (
            _module_report(
                "image_preprocess",
                "skipped",
                "图片预处理默认关闭，避免 OpenCV 在启动阶段阻塞 MinerU；当前使用原始图片进入解析。",
                skipped_reason="如需启用图像增强，请设置 DATA_AGENT_ENABLE_IMAGE_PREPROCESS=true 后重启后端。",
                tool_calls=[
                    {
                        "tool": "opencv",
                        "status": "disabled",
                        "reason": "DATA_AGENT_ENABLE_IMAGE_PREPROCESS is false",
                    }
                ],
            ),
            None,
        )

    cv2 = _optional_import("cv2")
    np = _optional_import("numpy")
    if cv2 is None or np is None:
        return (
            _module_report(
                "image_preprocess",
                "skipped",
                "未安装 OpenCV/numpy，跳过图像增强。",
                skipped_reason="安装 opencv-python 与 numpy 后可启用去噪、锐化、对比度增强和局部阈值。",
                tool_calls=[{"tool": "opencv", "status": "not_available"}],
            ),
            None,
        )

    raw = np.fromfile(str(input_path), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if image is None:
        return (
            _module_report(
                "image_preprocess",
                "skipped",
                "OpenCV 无法读取该图片，继续使用原始输入。",
                skipped_reason="图片解码失败。",
                tool_calls=[{"tool": "opencv", "status": "decode_failed"}],
            ),
            None,
        )

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    contrast = float(gray.std())
    enhanced = gray.copy()
    actions: list[str] = []

    if blur_score < 120:
        blurred = cv2.GaussianBlur(enhanced, (0, 0), 3)
        enhanced = cv2.addWeighted(enhanced, 1.6, blurred, -0.6, 0)
        actions.append("sharpen")

    if contrast < 45:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(enhanced)
        actions.append("clahe_contrast")

    if brightness < 80 or brightness > 200:
        enhanced = cv2.adaptiveThreshold(
            enhanced,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )
        actions.append("adaptive_threshold")

    quality = {
        "blur_score": round(blur_score, 2),
        "brightness": round(brightness, 2),
        "contrast": round(contrast, 2),
    }
    if not actions:
        return (
            _module_report(
                "image_preprocess",
                "pass",
                "图片质量指标未触发增强规则，继续使用原始输入。",
                details={"quality": quality, "actions": []},
                tool_calls=[{"tool": "opencv", "status": "available"}],
            ),
            None,
        )

    enhanced_path = Path(record.output_dir) / "enhancements" / "preprocess" / f"{input_path.stem}_enhanced.png"
    enhanced_path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", enhanced)
    if not ok:
        return (
            _module_report(
                "image_preprocess",
                "skipped",
                "增强图片编码失败，继续使用原始输入。",
                skipped_reason="OpenCV imencode 失败。",
                details={"quality": quality, "actions": actions},
                tool_calls=[{"tool": "opencv", "status": "encode_failed"}],
            ),
            None,
        )
    encoded.tofile(str(enhanced_path))
    return (
        _module_report(
            "image_preprocess",
            "applied",
            "已生成增强版图片，并将其作为 MinerU 的有效输入。",
            details={"quality": quality, "actions": actions, "effective_input_path": str(enhanced_path)},
            artifacts=[str(enhanced_path)],
            tool_calls=[{"tool": "opencv", "status": "called", "actions": actions}],
        ),
        str(enhanced_path),
    )


def run_pre_mineru_enhancements(record: JobRecord) -> dict[str, Any]:
    image_report, effective_input_path = _image_preprocess(record)
    return {
        "generated_at": _utc_now(),
        "effective_input_path": effective_input_path or record.input_path,
        "modules": [image_report],
    }


def build_pre_enhancement_timeout_report(record: JobRecord, timeout_seconds: float) -> dict[str, Any]:
    return {
        "generated_at": _utc_now(),
        "effective_input_path": record.input_path,
        "modules": [
            _module_report(
                "image_preprocess",
                "skipped",
                f"前置增强超过 {timeout_seconds:.0f} 秒未完成，已跳过并继续使用原始输入。",
                skipped_reason="pre-enhancement timeout",
                warnings=["前置增强超时不会中断主解析流程。"],
            )
        ],
    }


def _financial_table_validation(output_dir: Path, manifest: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    documents = manifest.get("documents", [])
    by_type: dict[str, int] = {}
    if documents and isinstance(documents, list):
        for document in documents:
            summary = document.get("summary", {}) if isinstance(document, dict) else {}
            for key, value in summary.get("by_type", {}).items():
                by_type[key] = by_type.get(key, 0) + int(value or 0)

    table_chunks = [chunk for chunk in chunks if str(chunk.get("type", "")).lower() == "table"]
    all_text = "\n".join(_chunk_text(chunk) for chunk in chunks)
    keyword_hits = sorted({keyword for keyword in FINANCE_KEYWORDS if keyword.lower() in all_text.lower()})
    numeric_tokens = NUMBER_PATTERN.findall(all_text)
    dense_numeric_chunks = [
        {
            "element_id": chunk.get("element_id"),
            "page_idx": chunk.get("page_idx"),
            "type": chunk.get("type"),
            "number_count": len(NUMBER_PATTERN.findall(_chunk_text(chunk))),
        }
        for chunk in chunks
        if len(NUMBER_PATTERN.findall(_chunk_text(chunk))) >= 8
    ][:50]

    suspicious_ocr = re.findall(r"\d[OoIl]\d", all_text)
    invalid_commas = [
        token
        for token in numeric_tokens
        if "," in token and not re.match(r"^\(?[-+]?\d{1,3}(,\d{3})+(?:\.\d+)?%?\)?$", token)
    ][:50]
    total_line_candidates = [
        line.strip()
        for line in all_text.splitlines()
        if any(word in line.lower() for word in ("合计", "小计", "total", "subtotal")) and NUMBER_PATTERN.search(line)
    ][:50]

    details = {
        "table_elements": by_type.get("table", 0) or len(table_chunks),
        "numeric_token_count": len(numeric_tokens),
        "dense_numeric_chunk_count": len(dense_numeric_chunks),
        "finance_keyword_hits": keyword_hits,
        "dense_numeric_chunks": dense_numeric_chunks,
        "total_line_candidates": total_line_candidates,
        "suspicious_ocr_patterns": suspicious_ocr[:50],
        "invalid_comma_numbers": invalid_commas,
    }
    artifact = _write_json(output_dir / "enhancements" / "financial_table_validation.json", details)

    if not keyword_hits and not table_chunks and len(dense_numeric_chunks) < 2:
        return _module_report(
            "financial_table_validator",
            "skipped",
            "未检测到明显财务报表或密集数字表格特征。",
            details=details,
            artifacts=[artifact],
            skipped_reason="缺少财务关键词、表格元素或密集数字片段。",
            tool_calls=[{"tool": "rule_based_financial_validator", "status": "called"}],
        )

    warnings: list[str] = []
    if suspicious_ocr:
        warnings.append("检测到疑似 OCR 数字混淆模式，例如数字中夹杂 O/I/l。")
    if invalid_commas:
        warnings.append("检测到逗号分组异常的数字，建议人工或规则复核。")
    status = "review" if warnings else "pass"
    summary = "已完成财报/密集数字表格校验，未发现明显格式风险。" if status == "pass" else "已完成财报/密集数字表格校验，存在需要复核的数字格式风险。"
    return _module_report(
        "financial_table_validator",
        status,
        summary,
        details=details,
        artifacts=[artifact],
        warnings=warnings,
        tool_calls=[{"tool": "rule_based_financial_validator", "status": "called"}],
    )


def _image_size(path: str) -> dict[str, Any]:
    pil_image = _optional_import("PIL.Image")
    if pil_image is None:
        return {}
    try:
        with pil_image.open(path) as image:
            return {"width": image.width, "height": image.height}
    except Exception:
        return {}


def _image_data_url(path: Path) -> str:
    pil_image = _optional_import("PIL.Image")
    if pil_image is not None:
        try:
            with pil_image.open(path) as image:
                image = image.convert("RGB")
                max_side = max(320, settings.chart_vlm_image_max_side)
                image.thumbnail((max_side, max_side))
                buffer = io.BytesIO()
                image.save(
                    buffer,
                    format="JPEG",
                    quality=max(40, min(95, settings.chart_vlm_image_quality)),
                    optimize=True,
                )
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
        except Exception:
            pass

    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _parse_json_or_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
        return {"items": parsed}
    except Exception:
        return {"raw_text": text}


def _call_openai_vlm_for_chart(candidate: dict[str, Any]) -> dict[str, Any]:
    media_path = candidate.get("media_path")
    if not isinstance(media_path, str) or not Path(media_path).exists():
        return {
            "status": "skipped",
            "reason": "candidate media_path missing or file does not exist",
        }
    image_path = Path(media_path)
    max_bytes = int(settings.chart_vlm_max_image_mb * 1024 * 1024)
    image_size = image_path.stat().st_size
    if image_size > max_bytes:
        return {
            "status": "skipped",
            "reason": f"image too large: {image_size} bytes > {max_bytes} bytes",
        }
    if not settings.chart_vlm_api_base or not settings.chart_vlm_api_key:
        return {
            "status": "failed",
            "reason": "DATA_AGENT_CHART_VLM_API_BASE/API_KEY or OPENAI_API_BASE/API_KEY is not configured",
        }

    httpx = _optional_import("httpx")
    if httpx is None:
        return {
            "status": "failed",
            "reason": "httpx is not installed",
        }

    prompt = (
        "你是文档图表解析器。请从图片中提取结构化信息，只返回 JSON。"
        "字段包含 chart_type、title、axes、legend、series、table_like_values、summary、confidence、warnings。"
        "如果不是图表而是普通图片，也要说明 image_type 和可见文字。"
    )
    payload = {
        "model": settings.chart_vlm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(image_path),
                        },
                    },
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": 1200,
    }
    url = settings.chart_vlm_api_base.rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=settings.chart_vlm_timeout_seconds, verify=settings.chart_vlm_verify_ssl) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {settings.chart_vlm_api_key}"},
                json=payload,
            )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return {
            "status": "parsed",
            "model": settings.chart_vlm_model,
            "result": _parse_json_or_text(content),
        }
    except Exception as exc:
        curl_result = _call_openai_vlm_with_curl(url, payload)
        if curl_result.get("status") == "parsed":
            curl_result["transport_fallback"] = {
                "from": "httpx",
                "reason": f"{exc.__class__.__name__}: {str(exc)[:300]}",
                "to": "curl",
            }
            return curl_result
        return {
            "status": "failed",
            "model": settings.chart_vlm_model,
            "reason": f"{exc.__class__.__name__}: {str(exc)[:1000]}",
            "curl_fallback": curl_result,
        }


def _call_openai_vlm_with_curl(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        return {
            "status": "failed",
            "model": settings.chart_vlm_model,
            "reason": "curl executable not found",
        }
    with tempfile.TemporaryDirectory(prefix="mineru_vlm_") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        response_path = Path(temp_dir) / "response.json"
        _write_json(request_path, payload)
        command = [
            curl_path,
            "-sS",
            "-L",
            "--http1.1",
            "--connect-timeout",
            str(max(5, int(settings.chart_vlm_timeout_seconds))),
            "--max-time",
            str(max(10, int(settings.chart_vlm_timeout_seconds) + 15)),
            "-H",
            f"Authorization: Bearer {settings.chart_vlm_api_key}",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            f"@{request_path}",
            "-o",
            str(response_path),
            url,
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=settings.chart_vlm_timeout_seconds + 25)
        if completed.returncode != 0:
            return {
                "status": "failed",
                "model": settings.chart_vlm_model,
                "reason": f"curl exit {completed.returncode}: {(completed.stderr or completed.stdout)[:1000]}",
            }
        if not response_path.exists():
            return {
                "status": "failed",
                "model": settings.chart_vlm_model,
                "reason": "curl did not create a response body",
            }
        try:
            response_payload = json.loads(response_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "status": "failed",
                "model": settings.chart_vlm_model,
                "reason": f"curl returned non-json response: {exc.__class__.__name__}: {response_path.read_text(encoding='utf-8', errors='replace')[:1000]}",
            }
        if isinstance(response_payload, dict) and response_payload.get("error"):
            return {
                "status": "failed",
                "model": settings.chart_vlm_model,
                "reason": f"curl API error: {json.dumps(response_payload.get('error'), ensure_ascii=False)[:1000]}",
                "response": response_payload,
            }
        try:
            content = response_payload["choices"][0]["message"]["content"]
        except Exception:
            return {
                "status": "failed",
                "model": settings.chart_vlm_model,
                "reason": "curl response missing choices[0].message.content",
                "response": response_payload,
            }
        return {
            "status": "parsed",
            "model": settings.chart_vlm_model,
            "transport": "curl",
            "result": _parse_json_or_text(content),
        }


def _chart_candidate_extraction(output_dir: Path, manifest: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for chunk in chunks:
        element_type = str(chunk.get("type", "")).lower()
        if element_type not in {"chart", "image", "figure"}:
            continue
        text = _chunk_text(chunk)
        media_path = chunk.get("media_path")
        candidate = {
            "document": chunk.get("document"),
            "element_id": chunk.get("element_id"),
            "type": chunk.get("type"),
            "page_idx": chunk.get("page_idx"),
            "text_preview": text[:240],
            "media_path": media_path,
        }
        if isinstance(media_path, str) and Path(media_path).exists():
            candidate["image_size"] = _image_size(media_path)
        candidates.append(candidate)

    for document in manifest.get("documents", []):
        if not isinstance(document, dict):
            continue
        for image_path in document.get("images", [])[:200]:
            if not any(candidate.get("media_path") == image_path for candidate in candidates):
                candidates.append(
                    {
                        "document": document.get("document"),
                        "element_id": None,
                        "type": "manifest_image",
                        "page_idx": None,
                        "text_preview": "",
                        "media_path": image_path,
                        "image_size": _image_size(image_path) if isinstance(image_path, str) else {},
                    }
                )

    chart_tool = settings.chart_tool
    details = {
        "candidate_count": len(candidates),
        "chart_tool": chart_tool or "not_configured",
        "chart_vlm_model": settings.chart_vlm_model if chart_tool in {"openai-vlm", "vlm"} else None,
        "candidates": candidates[:200],
    }
    artifacts = [_write_json(output_dir / "enhancements" / "chart_candidates.json", details)]
    if not candidates:
        return _module_report(
            "chart_parser",
            "skipped",
            "未检测到图表或图片候选区域。",
            details=details,
            artifacts=artifacts,
            skipped_reason="MinerU 输出中没有 chart/image/figure 元素。",
            tool_calls=[{"tool": "chart_candidate_extractor", "status": "called"}],
        )

    tool_calls = [{"tool": "chart_candidate_extractor", "status": "called"}]
    warnings = []
    if not chart_tool:
        warnings.append("未配置 DePlot/ChartOCR/VLM 图表解析后端，当前仅输出图表候选区域供后续工具消费。")
        tool_calls.append({"tool": "external_chart_parser", "status": "not_configured"})
    elif chart_tool in {"openai-vlm", "vlm"}:
        parsed_results = []
        for candidate in candidates[: max(0, settings.chart_vlm_max_candidates)]:
            result = _call_openai_vlm_for_chart(candidate)
            parsed_results.append({**candidate, "vlm": result})
        parsed_artifact = _write_json(output_dir / "enhancements" / "chart_vlm_results.json", {
            "tool": chart_tool,
            "model": settings.chart_vlm_model,
            "max_candidates": settings.chart_vlm_max_candidates,
            "results": parsed_results,
        })
        artifacts.append(parsed_artifact)
        parsed_count = sum(1 for item in parsed_results if item.get("vlm", {}).get("status") == "parsed")
        failed_count = sum(1 for item in parsed_results if item.get("vlm", {}).get("status") == "failed")
        skipped_count = sum(1 for item in parsed_results if item.get("vlm", {}).get("status") == "skipped")
        details["vlm_result_count"] = len(parsed_results)
        details["vlm_parsed_count"] = parsed_count
        details["vlm_failed_count"] = failed_count
        details["vlm_skipped_count"] = skipped_count
        details["vlm_results_preview"] = parsed_results[:20]
        tool_calls.append({
            "tool": "openai_compatible_vlm",
            "status": "called",
            "model": settings.chart_vlm_model,
            "parsed": parsed_count,
            "failed": failed_count,
            "skipped": skipped_count,
        })
        if failed_count:
            warnings.append("VLM 图表解析存在失败项，详情见 chart_vlm_results.json。")
    else:
        warnings.append(f"暂不支持的图表解析后端：{chart_tool}。当前仅输出候选区域。")
        tool_calls.append({"tool": chart_tool, "status": "unsupported"})
    status = "candidate_extracted"
    summary = f"已提取 {len(candidates)} 个图表/图片候选区域。"
    if details.get("vlm_result_count"):
        status = "parsed" if details.get("vlm_parsed_count") else "candidate_extracted"
        summary = (
            f"已提取 {len(candidates)} 个图表/图片候选区域，并调用 VLM 解析 "
            f"{details.get('vlm_result_count')} 个候选，成功 {details.get('vlm_parsed_count')} 个。"
        )
    return _module_report(
        "chart_parser",
        status,
        summary,
        details=details,
        artifacts=artifacts,
        warnings=warnings,
        tool_calls=tool_calls,
    )


def _signature(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    normalized = re.sub(r"[^\w\u4e00-\u9fff ]+", "", normalized)
    return normalized[:120]


def _similar(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _cross_page_analysis(output_dir: Path, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_chunks = sorted(
        chunks,
        key=lambda item: (
            str(item.get("document", "")),
            item.get("page_idx") if isinstance(item.get("page_idx"), int) else 10**9,
            item.get("element_index") if isinstance(item.get("element_index"), int) else 10**9,
        ),
    )

    table_chunks = [chunk for chunk in sorted_chunks if str(chunk.get("type", "")).lower() == "table"]
    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    previous_signature = ""
    previous_page: int | None = None
    previous_doc: str | None = None

    for chunk in table_chunks:
        doc = str(chunk.get("document", ""))
        page = chunk.get("page_idx") if isinstance(chunk.get("page_idx"), int) else None
        sig = _signature(_chunk_text(chunk))
        can_continue = (
            current
            and doc == previous_doc
            and page is not None
            and previous_page is not None
            and 0 <= page - previous_page <= 1
            and (_similar(sig, previous_signature) >= 0.35 or bool(sig and previous_signature))
        )
        if can_continue:
            current.append(chunk)
        else:
            if len(current) >= 2:
                groups.append(
                    {
                        "type": "multi_page_table_candidate",
                        "document": previous_doc,
                        "pages": sorted({item.get("page_idx") for item in current if item.get("page_idx") is not None}),
                        "element_ids": [item.get("element_id") for item in current],
                        "merge_reason": "相邻页面检测到连续表格元素，建议作为跨页表格候选复核。",
                    }
                )
            current = [chunk]
        previous_signature = sig
        previous_page = page
        previous_doc = doc

    if len(current) >= 2:
        groups.append(
            {
                "type": "multi_page_table_candidate",
                "document": previous_doc,
                "pages": sorted({item.get("page_idx") for item in current if item.get("page_idx") is not None}),
                "element_ids": [item.get("element_id") for item in current],
                "merge_reason": "相邻页面检测到连续表格元素，建议作为跨页表格候选复核。",
            }
        )

    references: list[dict[str, Any]] = []
    last_context_by_doc: dict[str, dict[str, Any]] = {}
    for chunk in sorted_chunks:
        doc = str(chunk.get("document", ""))
        text = _chunk_text(chunk).strip()
        if len(text) > 8 and not REFERENCE_PATTERN.search(text):
            last_context_by_doc[doc] = chunk
        if len(text) > 12 and REFERENCE_PATTERN.search(text):
            target = last_context_by_doc.get(doc)
            references.append(
                {
                    "type": "reference_resolution_candidate",
                    "document": doc,
                    "source_element_id": chunk.get("element_id"),
                    "source_page_idx": chunk.get("page_idx"),
                    "source_text_preview": text[:180],
                    "candidate_target_element_id": target.get("element_id") if target else None,
                    "candidate_target_page_idx": target.get("page_idx") if target else None,
                    "reason": "文本包含跨句或跨页指代词，建议结合上下文复核。",
                }
            )

    details = {
        "multi_page_table_candidates": groups[:100],
        "reference_resolution_candidates": references[:100],
        "scanned_chunk_count": len(sorted_chunks),
    }
    artifact = _write_json(output_dir / "enhancements" / "cross_page_objects.json", details)
    status = "candidate_extracted" if groups or references else "pass"
    summary = (
        f"发现 {len(groups)} 个跨页表格候选和 {len(references)} 个指代消解候选。"
        if groups or references
        else "已扫描跨页表格和全局指代，未发现明显候选对象。"
    )
    return _module_report(
        "cross_page_merge",
        status,
        summary,
        details=details,
        artifacts=[artifact],
        tool_calls=[{"tool": "rule_based_cross_page_analyzer", "status": "called"}],
    )


def run_post_mineru_enhancements(record: JobRecord, manifest: dict[str, Any], chunks_path: Path) -> dict[str, Any]:
    output_dir = Path(record.output_dir)
    chunks = _read_jsonl(chunks_path)
    modules = [
        _financial_table_validation(output_dir, manifest, chunks),
        _chart_candidate_extraction(output_dir, manifest, chunks),
        _cross_page_analysis(output_dir, chunks),
    ]
    return {
        "generated_at": _utc_now(),
        "modules": modules,
    }


def write_enhancement_report(record: JobRecord, pre_report: dict[str, Any], post_report: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    output_dir = Path(record.output_dir)
    report = {
        "generated_at": _utc_now(),
        "job_id": record.job_id,
        "routing": {
            "input_suffix": Path(record.input_path).suffix.lower(),
            "modules": [
                "image_preprocess",
                "financial_table_validator",
                "chart_parser",
                "cross_page_merge",
            ],
            "policy": "始终记录四类增强模块的执行或跳过原因；可选依赖缺失时不中断 MinerU 主流程。",
        },
        "pre_mineru": pre_report,
        "post_mineru": post_report,
    }
    path = output_dir / "enhancement_report.json"
    _write_json(path, report)
    return report, path
