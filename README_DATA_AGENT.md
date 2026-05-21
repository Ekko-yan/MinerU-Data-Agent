# MinerU Data Agent

This project turns the MinerU parser into the first module of a Data Agent for
document understanding and structured corpus production.

## Architecture

- `backend/`: FastAPI service with asynchronous jobs, agent logs, MinerU parsing,
  structured output generation, and optional LLM planning/quality review.
- `frontend/`: React + Vite console for uploading documents, monitoring jobs,
  and downloading structured artifacts.
- `process_documents_with_mineru.py`: standalone MinerU wrapper used by the
  backend as the first parsing module.

## Backend

Start the API service:

```powershell
$env:OPENAI_API_BASE="https://bk.xiaozhiai.cc/v1"
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_MODEL="glm-4"
.\.venv_mineru\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

The LLM environment variables are optional. Without them, the agent falls back
to rule-based planning and validation.

Useful endpoints:

- `GET /api/health`
- `POST /api/parse`: upload and parse a document, returning an async job id.
- `POST /api/jobs`: compatibility alias for `POST /api/parse`.
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/result`
- `GET /api/jobs/{job_id}/artifact/manifest`
- `GET /api/jobs/{job_id}/artifact/chunks`

## Frontend

Start the web console:

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Open:

```text
http://127.0.0.1:5173
```

If the backend runs on another address:

```powershell
$env:VITE_API_BASE="http://127.0.0.1:8765"
npm run dev -- --host 127.0.0.1 --port 5173
```

## Output

Each job writes artifacts under:

```text
agent_data/jobs/{job_id}/output
```

Important files:

- `structured_manifest.json`
- `structured_chunks.jsonl`
- `agent_result.json`

## Competition Capability Mapping

- Task 1, data understanding and structured processing: MinerU parses source
  documents into Markdown, `content_list`, `middle_json`, image artifacts, and
  normalized JSONL chunks.
- Task 2, planning and automatic execution: the backend generates a plan,
  executes MinerU, aggregates outputs, and records auditable multi-stage logs.
- Task 3, stability and comprehensive evaluation: asynchronous job records,
  persisted logs, artifact download endpoints, health checks, and fallback
  planning/quality checks support production-style evaluation.
