import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Copy,
  Download,
  FileJson,
  FileText,
  GitBranch,
  Loader2,
  Play,
  RefreshCw,
  Terminal,
  Trash2,
  Upload,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8765'

type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

type AgentLog = {
  time: string
  stage: string
  level: 'info' | 'warning' | 'error'
  message: string
  details: Record<string, unknown>
}

type JobRecord = {
  job_id: string
  status: JobStatus
  created_at: string
  updated_at: string
  input_filename: string
  output_dir: string
  logs: AgentLog[]
  result?: AgentResult | null
  error?: string | null
}

type JobProgress = {
  job_id: string
  status: JobStatus | 'orphaned'
  stage: string
  message: string
  percent?: number | null
  current?: number | null
  total?: number | null
  running_seconds?: number | null
  cli_tail: string
}

type AgentResult = {
  plan?: {
    goal?: unknown
    steps?: unknown
    risk_controls?: unknown
  }
  quality_report?: {
    verdict?: string
    checks?: unknown
    suggestions?: unknown
  }
  manifest?: {
    summary?: { documents?: number; chunks?: number }
  }
  chunks?: unknown
  chunks_preview?: unknown
  chunks_inline_truncated?: boolean
  enhancement_report?: {
    pre_mineru?: {
      modules?: unknown
    }
    post_mineru?: {
      modules?: unknown
    }
  }
}

const statusLabel: Record<JobStatus, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const stageLabel: Record<string, string> = {
  queue: '排队',
  understand: '理解',
  plan: '规划',
  execute: '执行',
  enhance: '增强',
  pages: '页面处理',
  'Layout Predict': '版面分析',
  structure: '结构化',
  verify: '校验',
  finished: '完成',
  failed: '失败',
  error: '错误',
}

const tabLabel = {
  plan: '计划',
  report: '报告',
  logs: '日志',
  terminal: '命令行',
  enhancements: '增强',
  chunks: '片段',
  files: '文件',
}

const textMap: Record<string, string> = {
  'Parse the document into Markdown and normalized structured JSON chunks.':
    '将文档解析为 Markdown，并生成规范化的结构化 JSON 片段。',
  'Validate input type and create a task workspace.': '校验输入类型，并创建独立任务工作区。',
  'Call MinerU with the selected backend and parsing options.': '按所选后端和解析参数调用 MinerU。',
  'Collect Markdown, content_list, middle_json, images, and metadata.':
    '收集 Markdown、content_list、middle_json、图片和元数据。',
  'Normalize parsed elements into JSONL chunks.': '将解析元素规范化为 JSONL 结构化片段。',
  'Verify output completeness and generate a reproducible processing log.':
    '检查输出完整性，并生成可复现的处理日志。',
  'Keep original MinerU outputs for auditability.': '保留 MinerU 原始输出，便于审计和复现。',
  'Return file paths and element counts for downstream validation.': '返回文件路径和元素统计，便于下游校验。',
  'Surface parser warnings in task logs.': '在任务日志中暴露解析警告和异常信息。',
  'The manifest indicates 1 document, which matches the provided document list.':
    '结构化索引显示 1 个文档，与输入文档一致。',
  'The manifest indicates 272 chunks; the sample chunk preview is consistent with chunk metadata.':
    '结构化索引显示 272 个片段，片段预览与元数据一致。',
  'Only a small preview of chunks is provided. Unable to verify completeness of all 272 chunks from the preview.':
    '当前只展示少量片段预览，无法仅凭预览确认全部 272 个片段的完整性。',
  'The provided text excerpts appear coherent and consistent with the document content, but the last chunk preview is truncated, indicating incomplete data.':
    '文本片段整体连贯且与文档内容一致，但预览会被截断，因此不能仅凭预览判断完整内容。',
  'Document paths and related metadata fields are consistent and appear complete.':
    '文档路径和相关元数据字段一致，整体看起来完整。',
  'Task queued and waiting for an available MinerU worker.': '任务已进入队列，等待可用的 MinerU worker。',
  'Task received and input validation completed.': '任务已接收，输入校验完成。',
  'Calling the LLM planner.': '正在调用 LLM 生成执行计划。',
  'Execution plan generated.': '执行计划已生成。',
  'Calling the MinerU structured parsing module.': '正在调用 MinerU 结构化解析模块。',
  'MinerU execution finished.': 'MinerU 执行已结束。',
  'Aggregating Markdown, JSON, and element-level JSONL outputs.': '正在汇总 Markdown、JSON 和元素级 JSONL 输出。',
  'Structured outputs generated.': '结构化输出已生成。',
  'Calling the LLM quality reviewer.': '正在调用 LLM 进行质量检查。',
  'Quality review completed.': '质量检查已完成。',
  'MinerU process disappeared before completion.': 'MinerU 进程在任务完成前消失。',
  'MinerU failed with exit code 0.': 'MinerU 已成功退出。',
  'Verify full content extraction from source document to ensure no truncation.':
    '建议核对源文档的完整抽取情况，确认没有内容截断。',
  'Check parsing logs or error reports for failures causing incomplete text capture.':
    '建议检查解析日志或错误报告，定位可能导致文本抽取不完整的问题。',
  'Confirm presence and correctness of reference citation links and formatting.':
    '建议确认参考文献链接和格式是否完整正确。',
  'Ensure all chunks are properly linked and assembled into coherent document structure.':
    '建议确认全部片段能够正确关联并组成连贯的文档结构。',
  'Provide full chunk data or a summary coverage report to verify completeness of all 272 chunks.':
    '建议提供完整片段数据或覆盖率报告，以验证全部 272 个片段的完整性。',
  'Ensure that chunk text previews are not truncated to confirm full data integrity.':
    '建议在质检时使用完整片段文本，而不是截断后的预览。',
  'Include validation of content_list and model_json files to ensure no data loss or parse errors.':
    '建议增加对 content_list 和 model_json 的校验，确认没有数据丢失或解析错误。',
}

const checkNameMap: Record<string, string> = {
  document_count: '文档数量',
  chunk_count: '结构片段数量',
  chunk_coverage: '片段覆盖率',
  content_integrity: '内容完整性',
  metadata_consistency: '元数据一致性',
}

const statusTextMap: Record<string, string> = {
  pass: '通过',
  partial: '部分通过',
  warning: '警告',
  fail: '失败',
  review: '需复核',
}

function localizeText(value: string): string {
  return textMap[value] ?? statusTextMap[value.toLowerCase()] ?? value
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string').map(localizeText)
  }
  if (typeof value === 'string') {
    return value
      .split(/\s*(?:\n|(?<=\.)\s+(?=[A-Z])|[；;])\s*/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map(localizeText)
  }
  return []
}

function asChecks(value: unknown): Array<{ name: string; status: string; message: string }> {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object')
    .map((item) => ({
      name: checkNameMap[String(item.name ?? 'check')] ?? String(item.name ?? 'check'),
      status: localizeText(String(item.status ?? 'unknown')),
      message: localizeText(String(item.message ?? '')),
    }))
}

function asChunks(value: unknown): Array<{
  element_id: string
  type: string
  page_idx?: number
  text?: string
  media_path?: string | null
}> {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object')
    .map((item, index) => ({
      element_id: String(item.element_id ?? `chunk:${index}`),
      type: String(item.type ?? 'unknown'),
      page_idx: typeof item.page_idx === 'number' ? item.page_idx : undefined,
      text: typeof item.text === 'string' ? item.text : undefined,
      media_path: typeof item.media_path === 'string' ? item.media_path : null,
    }))
}

function asSuggestionArray(value: unknown): string[] {
  return asStringArray(value)
}

function hasLogDetails(details: Record<string, unknown>): boolean {
  return Object.keys(details).length > 0
}

function formatLogDetails(details: Record<string, unknown>): string {
  return JSON.stringify(details, null, 2)
}

function isImageFile(file: File): boolean {
  return /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(file.name)
}

function asEnhancementModules(value: unknown): Array<{
  name: string
  status: string
  summary: string
  skipped_reason?: string
  warnings: string[]
  artifacts: string[]
  tool_calls: Array<{ tool?: unknown; status?: unknown }>
}> {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object')
    .map((item) => ({
      name: String(item.name ?? 'module'),
      status: String(item.status ?? 'unknown'),
      summary: String(item.summary ?? ''),
      skipped_reason: typeof item.skipped_reason === 'string' ? item.skipped_reason : undefined,
      warnings: Array.isArray(item.warnings)
        ? item.warnings.filter((warning): warning is string => typeof warning === 'string')
        : [],
      artifacts: Array.isArray(item.artifacts)
        ? item.artifacts.filter((artifact): artifact is string => typeof artifact === 'string')
        : [],
      tool_calls: Array.isArray(item.tool_calls)
        ? item.tool_calls.filter((call): call is Record<string, unknown> => call !== null && typeof call === 'object')
        : [],
    }))
}

function markdownEscape(value: unknown): string {
  return String(value ?? '')
    .replace(/\|/g, '\\|')
    .replace(/\r?\n/g, ' ')
    .trim()
}

function markdownList(items: string[], emptyText = '暂无'): string {
  if (!items.length) {
    return `- ${emptyText}`
  }
  return items.map((item) => `- ${item}`).join('\n')
}

function buildMarkdownReport(job: JobRecord, fullResult?: AgentResult): string {
  const result = fullResult ?? job.result
  const planGoal = typeof result?.plan?.goal === 'string' ? localizeText(result.plan.goal) : '暂无执行目标'
  const planSteps = asStringArray(result?.plan?.steps)
  const riskControls = asStringArray(result?.plan?.risk_controls)
  const checks = asChecks(result?.quality_report?.checks)
  const suggestions = asSuggestionArray(result?.quality_report?.suggestions)
  const chunks = asChunks(result?.chunks ?? result?.chunks_preview)
  const modules = [
    ...asEnhancementModules(result?.enhancement_report?.pre_mineru?.modules),
    ...asEnhancementModules(result?.enhancement_report?.post_mineru?.modules),
  ]
  const summary = result?.manifest?.summary
  const logRows = job.logs
    .map(
      (log) =>
        `| ${markdownEscape(new Date(log.time).toLocaleString())} | ${markdownEscape(stageLabel[log.stage] ?? log.stage)} | ${markdownEscape(log.level)} | ${markdownEscape(localizeText(log.message))} |`,
    )
    .join('\n')
  const moduleRows = modules
    .map(
      (module) =>
        `| ${markdownEscape(enhancementName(module.name))} | ${markdownEscape(enhancementStatus(module.status))} | ${markdownEscape(module.summary)} |`,
    )
    .join('\n')
  const checkRows = checks
    .map(
      (check) =>
        `| ${markdownEscape(check.name)} | ${markdownEscape(check.status)} | ${markdownEscape(check.message)} |`,
    )
    .join('\n')
  const chunkBlocks = chunks
    .slice(0, 8)
    .map((chunk, index) => {
      const title = `${index + 1}. ${chunk.type} / ${chunk.element_id}`
      const body = chunk.text || chunk.media_path || '暂无文本内容'
      return `### ${title}\n\n${body}`
    })
    .join('\n\n')

  return [
    `# MinerU Data Agent 任务报告`,
    ``,
    `## 基本信息`,
    ``,
    `- 任务ID：${job.job_id}`,
    `- 文件名：${job.input_filename}`,
    `- 状态：${statusLabel[job.status]}`,
    `- 创建时间：${new Date(job.created_at).toLocaleString()}`,
    `- 更新时间：${new Date(job.updated_at).toLocaleString()}`,
    `- 文档数：${summary?.documents ?? 0}`,
    `- 结构片段数：${summary?.chunks ?? 0}`,
    result?.chunks_inline_truncated ? `- 说明：当前内联片段达到后端上限，完整 JSONL 请从“文件”页下载。` : '',
    ``,
    `## 执行目标`,
    ``,
    planGoal,
    ``,
    `## 执行步骤`,
    ``,
    markdownList(planSteps, '等待生成执行计划'),
    ``,
    `## 风险控制`,
    ``,
    markdownList(riskControls, '暂无风险控制信息'),
    ``,
    `## 质量检查`,
    ``,
    checkRows ? `| 检查项 | 状态 | 说明 |\n| --- | --- | --- |\n${checkRows}` : '暂无质量检查结果。',
    ``,
    suggestions.length ? `## 改进建议\n\n${markdownList(suggestions)}` : '',
    ``,
    `## 增强模块`,
    ``,
    moduleRows ? `| 模块 | 状态 | 摘要 |\n| --- | --- | --- |\n${moduleRows}` : '暂无增强模块结果。',
    ``,
    `## 运行日志`,
    ``,
    logRows ? `| 时间 | 阶段 | 级别 | 消息 |\n| --- | --- | --- | --- |\n${logRows}` : '暂无运行日志。',
    ``,
    `## 片段预览`,
    ``,
    chunkBlocks || '暂无结构化片段预览。',
  ]
    .filter((part) => part !== '')
    .join('\n')
}

function App() {
  const [file, setFile] = useState<File | null>(null)
  const [backend, setBackend] = useState('pipeline')
  const [method, setMethod] = useState('auto')
  const [lang, setLang] = useState('ch')
  const [useLlm, setUseLlm] = useState(true)
  const [formula, setFormula] = useState(true)
  const [table, setTable] = useState(true)
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [progressByJobId, setProgressByJobId] = useState<Record<string, JobProgress>>({})
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [resultByJobId, setResultByJobId] = useState<Record<string, AgentResult>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [deletingJobId, setDeletingJobId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const activeJobIdRef = useRef<string | null>(null)

  const activeJob = useMemo(() => {
    if (!jobs.length) {
      return undefined
    }
    return jobs.find((job) => job.job_id === activeJobId) ?? jobs[0]
  }, [activeJobId, jobs])

  const refreshJobs = useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/jobs`)
    if (!response.ok) {
      throw new Error('任务列表加载失败')
    }
    const data = (await response.json()) as JobRecord[]
    setJobs(data)
    setActiveJobId((current) => {
      activeJobIdRef.current = current
      if (!data.length) {
        return null
      }
      if (current && data.some((job) => job.job_id === current)) {
        return current
      }
      return data[0].job_id
    })
  }, [])

  useEffect(() => {
    activeJobIdRef.current = activeJobId
  }, [activeJobId])

  useEffect(() => {
    refreshJobs().catch(() => undefined)
  }, [refreshJobs])

  const hasLiveJobs = jobs.some((job) => job.status === 'queued' || job.status === 'running')

  useEffect(() => {
    if (!hasLiveJobs) {
      return
    }
    const timer = window.setInterval(() => {
      refreshJobs().catch(() => undefined)
    }, 2500)
    return () => window.clearInterval(timer)
  }, [hasLiveJobs, refreshJobs])

  useEffect(() => {
    if (!activeJob) {
      return
    }
    let cancelled = false
    const loadProgress = async () => {
      const response = await fetch(`${API_BASE}/api/jobs/${activeJob.job_id}/progress?tail=12000`)
      if (!response.ok) {
        return
      }
      const data = (await response.json()) as JobProgress
      if (!cancelled) {
        setProgressByJobId((current) => ({ ...current, [activeJob.job_id]: data }))
      }
    }
    loadProgress().catch(() => undefined)
    if (activeJob.status !== 'queued' && activeJob.status !== 'running') {
      return () => {
        cancelled = true
      }
    }
    const timer = window.setInterval(() => {
      loadProgress().catch(() => undefined)
    }, 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [activeJob])

  useEffect(() => {
    if (!activeJob || activeJob.status !== 'succeeded' || resultByJobId[activeJob.job_id]) {
      return
    }
    let cancelled = false
    const loadFullResult = async () => {
      const response = await fetch(`${API_BASE}/api/jobs/${activeJob.job_id}/result`)
      if (!response.ok) {
        return
      }
      const data = (await response.json()) as AgentResult
      if (!cancelled) {
        setResultByJobId((current) => ({ ...current, [activeJob.job_id]: data }))
      }
    }
    loadFullResult().catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [activeJob, resultByJobId])

  const submitJob = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!file) {
      setNotice('请先选择一个文档。')
      return
    }
    setIsSubmitting(true)
    setNotice('')
    const form = new FormData()
    form.append('file', file)
    form.append('backend', backend)
    form.append('method', method)
    form.append('lang', lang)
    form.append('use_llm', String(useLlm))
    form.append('formula', String(formula))
    form.append('table', String(table))
    try {
      const response = await fetch(`${API_BASE}/api/parse`, {
        method: 'POST',
        body: form,
      })
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(error.detail ?? '提交失败')
      }
      const data = await response.json()
      setActiveJobId(data.job_id)
      activeJobIdRef.current = data.job_id
      await refreshJobs()
      setNotice('任务已提交，Data Agent 正在处理。')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '提交失败')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleFileChange = (selectedFile: File | null) => {
    setFile(selectedFile)
    if (selectedFile && isImageFile(selectedFile)) {
      setMethod('ocr')
      setFormula(false)
      setTable(true)
      setNotice('检测到图片输入，已切换为 OCR 并关闭公式解析；财务表格建议保留表格解析。')
    }
  }

  const deleteJob = async (jobId: string) => {
    setDeletingJobId(jobId)
    setNotice('')
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${jobId}`, { method: 'DELETE' })
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(error.detail ?? '删除失败')
      }
      setJobs((current) => current.filter((job) => job.job_id !== jobId))
      setActiveJobId((current) => (current === jobId ? null : current))
      setNotice('任务已删除；如果任务仍在运行，后端已尝试终止对应进程。')
      await refreshJobs()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '删除失败')
    } finally {
      setDeletingJobId(null)
    }
  }

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <p className="eyebrow">MinerU Data Agent</p>
          <h1>文档结构化处理控制台</h1>
        </div>
        <button className="icon-button" type="button" onClick={() => refreshJobs()}>
          <RefreshCw size={18} />
          刷新
        </button>
      </section>

      <section className="workspace">
        <form className="submit-panel" onSubmit={submitJob}>
          <div className="panel-heading">
            <Upload size={20} />
            <div>
              <h2>提交文档</h2>
              <p>支持 PDF、图片、DOCX、PPTX、XLSX</p>
            </div>
          </div>

          <label className="dropzone">
            <input
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tiff,.docx,.pptx,.xlsx"
              onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
            />
            <FileText size={28} />
            <span>{file ? file.name : '选择一个文档'}</span>
          </label>

          <div className="field-grid">
            <label>
              后端
              <select value={backend} onChange={(event) => setBackend(event.target.value)}>
                <option value="pipeline">pipeline</option>
                <option value="hybrid-auto-engine">hybrid-auto-engine</option>
                <option value="vlm-auto-engine">vlm-auto-engine</option>
                <option value="hybrid-http-client">hybrid-http-client</option>
                <option value="vlm-http-client">vlm-http-client</option>
              </select>
            </label>
            <label>
              解析方式
              <select value={method} onChange={(event) => setMethod(event.target.value)}>
                <option value="auto">auto</option>
                <option value="txt">txt</option>
                <option value="ocr">ocr</option>
              </select>
            </label>
            <label>
              OCR 语言
              <select value={lang} onChange={(event) => setLang(event.target.value)}>
                <option value="ch">ch</option>
                <option value="en">en</option>
                <option value="ch_lite">ch_lite</option>
                <option value="japan">japan</option>
                <option value="korean">korean</option>
              </select>
            </label>
          </div>

          <div className="toggle-row">
            <label>
              <input type="checkbox" checked={useLlm} onChange={(event) => setUseLlm(event.target.checked)} />
              LLM 规划
            </label>
            <label>
              <input type="checkbox" checked={formula} onChange={(event) => setFormula(event.target.checked)} />
              公式解析
            </label>
            <label>
              <input type="checkbox" checked={table} onChange={(event) => setTable(event.target.checked)} />
              表格解析
            </label>
          </div>

          <button className="primary-button" type="submit" disabled={isSubmitting}>
            {isSubmitting ? <Loader2 className="spin" size={18} /> : <Play size={18} />}
            运行 Agent
          </button>
          {notice && <p className="notice">{notice}</p>}
        </form>

        <section className="results-panel">
          <div className="panel-heading split">
            <div>
              <h2>任务详情</h2>
              <p>{activeJob ? activeJob.input_filename : '暂无任务'}</p>
            </div>
            {activeJob && <StatusBadge status={activeJob.status} />}
          </div>

          {activeJob ? (
            <>
              <MetricStrip job={activeJob} />
              <ProgressPanel job={activeJob} progress={progressByJobId[activeJob.job_id]} />
              <ResultTabs job={activeJob} fullResult={resultByJobId[activeJob.job_id]} />
            </>
          ) : (
            <div className="empty-state">
              <Activity size={28} />
              <p>提交一个文档，启动第一个 Data Agent 任务。</p>
            </div>
          )}
        </section>
      </section>

      <section className="job-list">
        {jobs.slice(0, 12).map((job) => (
          <article className={job.job_id === activeJob?.job_id ? 'job-row active' : 'job-row'} key={job.job_id}>
            <button className="job-select" type="button" onClick={() => setActiveJobId(job.job_id)}>
              <span>{job.input_filename}</span>
              <StatusBadge status={job.status} />
            </button>
            <button
              aria-label={`删除 ${job.input_filename}`}
              className="delete-button"
              disabled={deletingJobId === job.job_id}
              title="删除任务"
              type="button"
              onClick={() => deleteJob(job.job_id)}
            >
              {deletingJobId === job.job_id ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
            </button>
          </article>
        ))}
      </section>
    </main>
  )
}

function StatusBadge({ status }: { status: JobStatus }) {
  const Icon =
    status === 'succeeded'
      ? CheckCircle2
      : status === 'failed' || status === 'cancelled'
        ? AlertTriangle
        : Loader2
  return (
    <span className={`status-badge ${status}`}>
      <Icon className={status === 'running' || status === 'queued' ? 'spin' : ''} size={14} />
      {statusLabel[status]}
    </span>
  )
}

function ProgressPanel({ job, progress }: { job: JobRecord; progress?: JobProgress }) {
  const percent = progress?.percent ?? (job.status === 'succeeded' ? 100 : 0)
  const stage = progress?.stage ? stageLabel[progress.stage] ?? progress.stage : statusLabel[job.status]
  const fallbackMessage =
    job.status === 'succeeded'
      ? '任务已完成，结构化结果已生成。'
      : job.status === 'failed'
        ? job.error || '任务执行失败，请查看日志或命令行输出。'
        : job.status === 'queued'
          ? '任务正在排队，等待 MinerU worker。'
          : '正在读取任务进度。'
  const pageText =
    progress?.total && progress.current !== null && progress.current !== undefined
      ? `${progress.current}/${progress.total}`
      : '等待进度单位'
  const seconds = progress?.running_seconds ? Math.floor(progress.running_seconds) : 0

  return (
    <div className="progress-panel">
      <div className="progress-heading">
        <div>
          <span>{stage}</span>
          <strong>{progress?.message ?? fallbackMessage}</strong>
        </div>
        <b>{percent ? `${percent}%` : pageText}</b>
      </div>
      <div className="progress-bar" aria-label="任务进度">
        <i style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />
      </div>
      <div className="progress-meta">
        <span>{pageText}</span>
        <span>运行 {seconds}s</span>
        <span>超时上限由后端环境变量控制</span>
      </div>
    </div>
  )
}

function MetricStrip({ job }: { job: JobRecord }) {
  const summary = job.result?.manifest?.summary
  const quality = job.result?.quality_report?.verdict ? localizeText(job.result.quality_report.verdict) : '等待中'
  return (
    <div className="metric-strip">
      <div>
        <span>文档数</span>
        <strong>{summary?.documents ?? 0}</strong>
      </div>
      <div>
        <span>结构片段</span>
        <strong>{summary?.chunks ?? 0}</strong>
      </div>
      <div>
        <span>质检结论</span>
        <strong>{quality}</strong>
      </div>
    </div>
  )
}

function ResultTabs({ job, fullResult }: { job: JobRecord; fullResult?: AgentResult }) {
  const [tab, setTab] = useState<'plan' | 'report' | 'logs' | 'terminal' | 'enhancements' | 'chunks' | 'files'>('plan')
  const [cliLog, setCliLog] = useState('')
  const result = fullResult ?? job.result
  const planGoal = typeof result?.plan?.goal === 'string' ? localizeText(result.plan.goal) : '等待生成执行计划'
  const planSteps = asStringArray(result?.plan?.steps)
  const riskControls = asStringArray(result?.plan?.risk_controls)
  const chunks = asChunks(result?.chunks ?? result?.chunks_preview)

  useEffect(() => {
    if (tab !== 'terminal') {
      return
    }
    let cancelled = false
    const loadCliLog = async () => {
      const response = await fetch(`${API_BASE}/api/jobs/${job.job_id}/cli-log?tail=30000`)
      const text = await response.text()
      if (!cancelled) {
        setCliLog(text)
      }
    }
    loadCliLog().catch(() => {
      if (!cancelled) {
        setCliLog('命令行日志读取失败。')
      }
    })
    const timer = window.setInterval(() => {
      loadCliLog().catch(() => undefined)
    }, 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [job.job_id, tab])

  return (
    <div className="tab-panel">
      <div className="tabs">
        {(['plan', 'report', 'logs', 'terminal', 'enhancements', 'chunks', 'files'] as const).map((item) => (
          <button className={tab === item ? 'active' : ''} key={item} type="button" onClick={() => setTab(item)}>
            {tabLabel[item]}
          </button>
        ))}
      </div>

      {tab === 'plan' && (
        <div className="stack">
          <h3>{planGoal}</h3>
          <ListBlock title="执行步骤" items={planSteps} />
          <ListBlock title="风险控制" items={riskControls} />
          <QualityBlock result={result} />
        </div>
      )}

      {tab === 'report' && <MarkdownReport job={job} fullResult={fullResult} />}

      {tab === 'logs' && (
        <div className="log-list">
          {job.logs.map((log, index) => (
            <div className={`log-item ${log.level}`} key={`${log.time}-${index}`}>
              <span>
                {stageLabel[log.stage] ?? log.stage}
                {log.level !== 'info' ? ` / ${log.level}` : ''}
              </span>
              <p>{localizeText(log.message)}</p>
              {hasLogDetails(log.details) && <pre>{formatLogDetails(log.details)}</pre>}
            </div>
          ))}
          {!job.logs.length && <p className="muted">暂无日志。</p>}
        </div>
      )}

      {tab === 'terminal' && (
        <div className="terminal-panel">
          <div className="terminal-heading">
            <Terminal size={16} />
            <span>MinerU CLI 实时输出</span>
          </div>
          <pre>{cliLog || '等待命令行日志...'}</pre>
        </div>
      )}

      {tab === 'enhancements' && <EnhancementBlock result={result} />}

      {tab === 'chunks' && (
        <div className="chunk-list">
          {chunks.map((chunk) => (
            <article className="chunk-item" key={chunk.element_id}>
              <div>
                <FileJson size={16} />
                <strong>{chunk.type}</strong>
                <span>{chunk.element_id}</span>
              </div>
              <p>{chunk.text || chunk.media_path || '暂无文本内容'}</p>
            </article>
          ))}
          {!chunks.length && <p className="muted">暂无结构化片段。</p>}
        </div>
      )}

      {tab === 'files' && (
        <div className="file-links">
          <a href={`${API_BASE}/api/jobs/${job.job_id}/artifact/manifest`} target="_blank">
            <Download size={16} />
            结构化索引 JSON
          </a>
          <a href={`${API_BASE}/api/jobs/${job.job_id}/artifact/chunks`} target="_blank">
            <Download size={16} />
            结构片段 JSONL
          </a>
          <a href={`${API_BASE}/api/jobs/${job.job_id}/artifact/agent_result`} target="_blank">
            <Download size={16} />
            Agent 结果 JSON
          </a>
          {job.result?.enhancement_report && (
            <a href={`${API_BASE}/api/jobs/${job.job_id}/artifact/enhancement_report`} target="_blank">
              <Download size={16} />
              增强报告 JSON
            </a>
          )}
        </div>
      )}
    </div>
  )
}

function EnhancementBlock({ result }: { result?: AgentResult | null }) {
  const preModules = asEnhancementModules(result?.enhancement_report?.pre_mineru?.modules)
  const postModules = asEnhancementModules(result?.enhancement_report?.post_mineru?.modules)
  const modules = [...preModules, ...postModules]

  return (
    <div className="enhancement-list">
      {modules.map((module) => (
        <article className="enhancement-item" key={module.name}>
          <div>
            <GitBranch size={16} />
            <strong>{enhancementName(module.name)}</strong>
            <span>{enhancementStatus(module.status)}</span>
          </div>
          <p>{module.summary}</p>
          {module.skipped_reason && <p className="muted">跳过原因：{module.skipped_reason}</p>}
          {module.warnings.length > 0 && <p className="muted">风险提示：{module.warnings.join('；')}</p>}
          {module.tool_calls.length > 0 && (
            <p className="muted">
              工具调用：
              {module.tool_calls
                .map((call) => `${String(call.tool ?? 'tool')}=${String(call.status ?? 'unknown')}`)
                .join('，')}
            </p>
          )}
          {module.artifacts.length > 0 && <p className="muted">产物：{module.artifacts.join('；')}</p>}
        </article>
      ))}
      {!modules.length && <p className="muted">等待增强模块结果。</p>}
    </div>
  )
}

function MarkdownReport({ job, fullResult }: { job: JobRecord; fullResult?: AgentResult }) {
  const markdown = buildMarkdownReport(job, fullResult)
  const [copied, setCopied] = useState(false)

  const copyMarkdown = async () => {
    await navigator.clipboard.writeText(markdown)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <div className="markdown-report">
      <div className="report-toolbar">
        <div>
          <strong>Markdown 任务报告</strong>
          <span>由 Agent 结果 JSON 自动生成</span>
        </div>
        <button type="button" onClick={copyMarkdown}>
          <Copy size={16} />
          {copied ? '已复制' : '复制 Markdown'}
        </button>
      </div>
      <div className="report-layout">
        <MarkdownPreview markdown={markdown} />
        <div className="markdown-source">
          <div>Markdown 源文</div>
          <pre>{markdown}</pre>
        </div>
      </div>
    </div>
  )
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  const lines = markdown.split('\n')
  const blocks: ReactNode[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index].trim()
    if (!line) {
      index += 1
      continue
    }
    if (line.startsWith('# ')) {
      blocks.push(<h1 key={index}>{line.slice(2)}</h1>)
      index += 1
      continue
    }
    if (line.startsWith('## ')) {
      blocks.push(<h2 key={index}>{line.slice(3)}</h2>)
      index += 1
      continue
    }
    if (line.startsWith('### ')) {
      blocks.push(<h3 key={index}>{line.slice(4)}</h3>)
      index += 1
      continue
    }
    if (line.startsWith('- ')) {
      const items: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('- ')) {
        items.push(lines[index].trim().slice(2))
        index += 1
      }
      blocks.push(
        <ul key={index}>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>,
      )
      continue
    }
    if (line.startsWith('|')) {
      const tableLines: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        tableLines.push(lines[index].trim())
        index += 1
      }
      blocks.push(<MarkdownTable key={index} lines={tableLines} />)
      continue
    }
    blocks.push(<p key={index}>{line}</p>)
    index += 1
  }
  return <article className="markdown-preview">{blocks}</article>
}

function splitMarkdownRow(line: string): string[] {
  const cells: string[] = []
  let current = ''
  for (let index = 1; index < line.length - 1; index += 1) {
    const char = line[index]
    if (char === '|' && line[index - 1] !== '\\') {
      cells.push(current.replace(/\\\|/g, '|').trim())
      current = ''
    } else {
      current += char
    }
  }
  cells.push(current.replace(/\\\|/g, '|').trim())
  return cells
}

function MarkdownTable({ lines }: { lines: string[] }) {
  const rows = lines
    .filter((line) => !/^\|\s*-+/.test(line))
    .map(splitMarkdownRow)
  const [head, ...body] = rows
  if (!head) {
    return null
  }
  return (
    <div className="report-table-wrap">
      <table>
        <thead>
          <tr>
            {head.map((cell) => (
              <th key={cell}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={`${rowIndex}-${row.join('|')}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${cellIndex}-${cell}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function enhancementName(name: string): string {
  const names: Record<string, string> = {
    image_preprocess: '图像预处理',
    financial_table_validator: '财报/密集数字校验',
    chart_parser: '复杂图表候选解析',
    cross_page_merge: '跨页合并与指代分析',
  }
  return names[name] ?? name
}

function enhancementStatus(status: string): string {
  const statuses: Record<string, string> = {
    applied: '已应用',
    pass: '通过',
    review: '需复核',
    skipped: '已跳过',
    candidate_extracted: '已提取候选',
    parsed: '已解析',
  }
  return statuses[status] ?? status
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h4>{title}</h4>
      {items.length ? (
        <ol>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      ) : (
        <p className="muted">等待中</p>
      )}
    </div>
  )
}

function QualityBlock({ result }: { result?: AgentResult | null }) {
  const checks = asChecks(result?.quality_report?.checks)
  const suggestions = asSuggestionArray(result?.quality_report?.suggestions)
  return (
    <div>
      <h4>质量检查</h4>
      <div className="check-list">
        {checks.map((check) => (
          <div className="check-item" key={check.name}>
            <strong>{check.status}</strong>
            <span>{check.name}</span>
            <p>{check.message}</p>
          </div>
        ))}
        {!checks.length && <p className="muted">等待中</p>}
      </div>
      {suggestions.length > 0 && (
        <div className="suggestion-list">
          <h4>改进建议</h4>
          <ol>
            {suggestions.map((suggestion) => (
              <li key={suggestion}>{suggestion}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

export default App
