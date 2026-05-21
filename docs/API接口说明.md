# MinerU Data Agent API 接口说明

服务基址：

```text
http://127.0.0.1:8765
```

当前本地版本默认不启用接口鉴权。若需要互联网可访问服务，建议在反向代理层配置：

```http
Authorization: Bearer <EVALUATION_TOKEN>
```

后端本体不保存真实模型密钥，LLM 服务密钥通过环境变量 `OPENAI_API_KEY` 注入。

## 1. 健康检查

```http
GET /api/health
```

返回示例：

```json
{
  "status": "ok",
  "service": "mineru-data-agent",
  "mineru_available": true,
  "llm_configured": true,
  "supported_inputs": [".pdf", ".docx", ".pptx", ".xlsx", ".png"]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 服务状态 |
| `mineru_available` | boolean | MinerU CLI 是否可用 |
| `llm_configured` | boolean | 是否配置 LLM 规划/质检 |
| `supported_inputs` | array | 支持的输入后缀 |

## 2. 创建解析任务

```http
POST /api/parse
POST /api/jobs
Content-Type: multipart/form-data
```

`/api/jobs` 是 `/api/parse` 的兼容别名。

参数说明：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `file` | file | 是 | 无 | PDF、图片、DOCX、PPTX、XLSX |
| `backend` | string | 否 | `pipeline` | MinerU 后端，如 `pipeline`、`hybrid-auto-engine`、`vlm-auto-engine` |
| `method` | string | 否 | `auto` | PDF 解析方式：`auto`、`txt`、`ocr` |
| `lang` | string | 否 | `ch` | OCR 语言提示 |
| `formula` | boolean | 否 | `true` | 是否启用公式解析 |
| `table` | boolean | 否 | `true` | 是否启用表格解析 |
| `image_analysis` | boolean | 否 | `true` | 是否启用图片/图表分析 |
| `model_source` | string | 否 | `modelscope` | MinerU 模型源 |
| `use_llm` | boolean | 否 | `true` | 是否启用 LLM 规划和质量检查 |
| `api_url` | string | 否 | 空 | 已有 mineru-api 地址 |
| `server_url` | string | 否 | 空 | VLM/http-client 后端模型服务地址 |
| `start_page` | integer | 否 | `0` | 起始页，0 基 |
| `end_page` | integer | 否 | 空 | 结束页 |

PowerShell 调用示例：

```powershell
$form = @{
  file = Get-Item "D:\vscodepro\MinerU\agent_data\jobs\02876e2fbe44429894c09ddd50dc7ab4\input\1.docx"
  backend = "pipeline"
  method = "auto"
  lang = "ch"
  formula = "true"
  table = "true"
  use_llm = "false"
}
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/parse -Form $form
```

返回示例：

```json
{
  "job_id": "bbe4981c8e414498b69bbacf9746fdaf",
  "status": "queued",
  "status_url": "/api/jobs/bbe4981c8e414498b69bbacf9746fdaf",
  "result_url": "/api/jobs/bbe4981c8e414498b69bbacf9746fdaf/result"
}
```

## 3. 查询任务列表

```http
GET /api/jobs
```

返回 `JobRecord[]`，按更新时间倒序排列。任务状态包括：

| 状态 | 含义 |
| --- | --- |
| `queued` | 已排队 |
| `running` | 正在运行 |
| `succeeded` | 成功 |
| `failed` | 失败 |
| `cancelled` | 已取消 |

## 4. 查询单个任务

```http
GET /api/jobs/{job_id}
```

返回示例：

```json
{
  "job_id": "34d2004785ed4f188517bcfa073d7316",
  "status": "succeeded",
  "input_filename": "2211_reduced_policy_optimization_fo.pdf",
  "output_dir": "D:\\vscodepro\\MinerU\\agent_data\\jobs\\34d2004785ed4f188517bcfa073d7316\\output",
  "logs": [
    {
      "time": "2026-05-21T05:32:25.008859Z",
      "stage": "understand",
      "level": "info",
      "message": "Task received and input validation completed.",
      "details": {
        "filename": "2211_reduced_policy_optimization_fo.pdf"
      }
    }
  ],
  "result": {
    "status": "succeeded",
    "manifest_path": "...\\structured_manifest.json",
    "chunks_path": "...\\structured_chunks.jsonl"
  },
  "error": null
}
```

说明：历史任务日志保留原始执行记录，新任务会直接写入中文日志；前端会对历史英文日志做中文本地化展示。

## 5. 查询实时进度

```http
GET /api/jobs/{job_id}/progress?tail=12000
```

返回示例：

```json
{
  "job_id": "432c6188811340a6a30c6f06ce917e5e",
  "status": "failed",
  "stage": "failed",
  "message": "MinerU exceeded timeout of 1800 seconds.",
  "percent": 71.1,
  "current": 224,
  "total": 315,
  "running_seconds": 1980.2,
  "cli_tail": "MFR Predict: 71% | 224/315 ..."
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `stage` | string | 当前执行阶段 |
| `percent` | number/null | 估算进度百分比 |
| `current` | integer/null | 当前处理单元 |
| `total` | integer/null | 总处理单元 |
| `running_seconds` | number/null | 运行时长 |
| `cli_tail` | string | MinerU 命令行日志尾部 |

## 6. 查看 MinerU 命令行日志

```http
GET /api/jobs/{job_id}/cli-log?tail=30000
```

返回 `text/plain`，用于排查 `Layout Predict`、`MFR Predict`、模型加载、超时等问题。

## 7. 获取最终结果

```http
GET /api/jobs/{job_id}/result
```

任务完成前返回 `202 Accepted`；失败时返回 `500` 和错误信息；成功时返回：

```json
{
  "job_id": "34d2004785ed4f188517bcfa073d7316",
  "status": "succeeded",
  "plan": {
    "goal": "将文档解析为 Markdown，并生成规范化的结构化 JSON 片段。",
    "steps": ["校验输入类型，并创建独立任务工作区。"]
  },
  "quality_report": {
    "verdict": "partial",
    "checks": []
  },
  "manifest_path": "...\\structured_manifest.json",
  "chunks_path": "...\\structured_chunks.jsonl",
  "manifest": {
    "summary": {
      "documents": 1,
      "chunks": 272
    }
  },
  "chunks_preview": []
}
```

## 8. 下载结果文件

```http
GET /api/jobs/{job_id}/artifact/manifest
GET /api/jobs/{job_id}/artifact/chunks
GET /api/jobs/{job_id}/artifact/agent_result
GET /api/jobs/{job_id}/artifact/cli_log
```

| 名称 | 文件 | 说明 |
| --- | --- | --- |
| `manifest` | `structured_manifest.json` | 文档级索引、解析产物路径、元素类型统计 |
| `chunks` | `structured_chunks.jsonl` | 元素级结构化片段 |
| `agent_result` | `agent_result.json` | Data Agent 最终输出 |
| `enhancement_report` | `enhancement_report.json` | 图像预处理、财报校验、图表候选、跨页合并增强报告 |
| `cli_log` | `mineru_cli.log` | MinerU 原始命令行日志 |

## 9. 删除任务

```http
DELETE /api/jobs/{job_id}
```

返回示例：

```json
{
  "job_id": "432c6188811340a6a30c6f06ce917e5e",
  "deleted": true,
  "cancelled_process": true
}
```

删除逻辑：

- 尝试终止已注册的 MinerU 进程树。
- 扫描命令行中包含 `job_id` 的残留进程并清理。
- 删除 `agent_data/jobs/{job_id}` 任务目录。
- 若任务记录已不存在但输出目录仍在，也允许清理残留目录。

## 10. 错误码

| HTTP 状态 | 场景 |
| --- | --- |
| `400` | 文件后缀不支持、任务 ID 非法 |
| `404` | 任务或结果文件不存在 |
| `202` | 任务尚未完成 |
| `500` | MinerU 执行失败、任务超时或后处理失败 |
