import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { BarChart3, Check, ChevronDown, ChevronRight, Copy, Send } from 'lucide-react'
import { api } from '../../api'
import type { Nl2sqlFormatOutput, Nl2sqlPhaseEvent, Nl2sqlStep } from '../../types'
import { PageHeader, cn } from '../ui'
import { Nl2SqlChart } from './Nl2SqlChart'

type SectionStatus = 'idle' | 'running' | 'done' | 'error'

interface Turn {
  id: string
  question: string
  pending: boolean
  error: string | null
  understand: {
    status: SectionStatus
    entityExplain?: string
    entities?: { time?: string[]; other?: string[]; metric?: string[] }
    candidates?: Record<string, Array<Record<string, unknown>>>
  }
  generate: { status: SectionStatus; tables?: string[]; rows?: number; attempts?: number }
  sql: string | null
  explanation: string | null
  result: { status: SectionStatus; outputs: Nl2sqlFormatOutput[] }
  /** 手风琴当前展开段：phase start 自动跟随，用户点击覆盖（再点收起为 null） */
  openStep: Nl2sqlStep | null
}

const STEP_TITLES: Record<Nl2sqlStep, string> = {
  understand: '问题理解',
  generate: '查询生成',
  result: '结果展示',
}

function applyPhaseEvent(turn: Turn, event: Nl2sqlPhaseEvent): Turn {
  const sectionStatus: SectionStatus = event.error ? 'error' : event.status === 'done' ? 'done' : 'running'
  if (event.status === 'start') {
    const next = { ...turn, openStep: event.step }
    if (event.step === 'understand') next.understand = { ...turn.understand, status: 'running' }
    if (event.step === 'generate') next.generate = { ...turn.generate, status: 'running' }
    if (event.step === 'result') next.result = { ...turn.result, status: 'running' }
    return next
  }
  if (event.step === 'understand') {
    return {
      ...turn,
      understand: {
        status: sectionStatus,
        entityExplain: event.entityExplain,
        entities: event.entities,
        candidates: event.candidates,
      },
    }
  }
  if (event.step === 'generate') {
    return {
      ...turn,
      generate: { status: sectionStatus, tables: event.tables, rows: event.rows, attempts: event.attempts },
    }
  }
  return { ...turn, result: { ...turn.result, status: sectionStatus } }
}

function StatusIcon({ status }: { status: SectionStatus }) {
  if (status === 'running') {
    return <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-300 border-t-ink" />
  }
  if (status === 'done') return <Check size={14} className="text-emerald-600" />
  if (status === 'error') return <span className="text-xs font-bold text-danger">×</span>
  return <span className="h-1.5 w-1.5 rounded-full bg-zinc-300" />
}

function StepCard(props: {
  title: string
  status: SectionStatus
  open: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <div className="rounded-md border border-line bg-panel">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left"
        onClick={props.onToggle}
      >
        {props.open ? <ChevronDown size={14} className="text-zinc-400" /> : <ChevronRight size={14} className="text-zinc-400" />}
        <span className="text-sm font-medium text-zinc-700">{props.title}</span>
        <span className="ml-auto"><StatusIcon status={props.status} /></span>
      </button>
      {props.open && <div className="border-t border-line px-3.5 py-3">{props.children}</div>}
    </div>
  )
}

/** 结果集表格：列 = 全部行键的并集（保序） */
function ResultTable({ data }: { data: Array<Record<string, unknown>> }) {
  const columns = Array.from(new Set(data.flatMap((row) => Object.keys(row))))
  if (columns.length === 0) return null
  return (
    <div className="thin-scrollbar mt-2 max-h-80 overflow-auto rounded-md border border-line">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0">
          <tr>
            {columns.map((column) => (
              <th key={column} className="border-b border-line bg-[#fafafa] px-3 py-2 text-xs font-semibold text-zinc-500">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-line last:border-0">
              {columns.map((column) => (
                <td key={column} className="px-3 py-2 text-sm text-zinc-700">{row[column] == null ? '' : String(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 独立问数页（决策⑥）：勾选数据集（≥2 个走跨数据集复合流）→ 自然语言提问
 *  → 三段式卡片（问题理解/查询生成/结果展示）。
 *  走真链路 POST /nl2sql/ask（SSE 分阶段推送）；figureType ≠ text 时结果卡内嵌 ECharts。 */
export function QueryDataView() {
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets })
  const enabledDatasets = (datasetsQuery.data ?? []).filter((item) => item.enabled)

  const [datasetIds, setDatasetIds] = React.useState<string[]>([])
  const didInitRef = React.useRef(false)
  const [draft, setDraft] = React.useState('')
  const [turns, setTurns] = React.useState<Turn[]>([])
  const [copiedId, setCopiedId] = React.useState<string | null>(null)
  const scrollRef = React.useRef<HTMLDivElement | null>(null)

  // 数据集清单加载后默认选第一个启用中的（仅首次；用户可自由增删勾选）
  React.useEffect(() => {
    if (!didInitRef.current && enabledDatasets.length > 0) {
      didInitRef.current = true
      setDatasetIds([enabledDatasets[0].id])
    }
  }, [enabledDatasets])

  const selectedDatasets = enabledDatasets.filter((item) => datasetIds.includes(item.id))
  const selectedNames = selectedDatasets.map((item) => `「${item.name}」`).join('、')
  // 跨数据集复合流要求同数据源（后端 409 同口径）；提前在选择栏提示
  const crossDatasource =
    new Set(selectedDatasets.map((item) => item.dataSourceId)).size > 1
  const noPromptNames = selectedDatasets
    .filter((item) => item.prompt.trim() === '')
    .map((item) => item.name)

  const toggleDataset = (id: string) => {
    setDatasetIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    )
    setTurns([]) // 切换选择清空对话（后端语义按数据集隔离）
  }

  const updateTurn = (turnId: string, updater: (turn: Turn) => Turn) => {
    setTurns((current) => current.map((turn) => (turn.id === turnId ? updater(turn) : turn)))
  }

  const send = () => {
    const question = draft.trim()
    if (!question || datasetIds.length === 0 || crossDatasource) return
    const turnId = `turn-${Date.now()}`
    setDraft('')
    setTurns((current) => [
      ...current,
      {
        id: turnId,
        question,
        pending: true,
        error: null,
        understand: { status: 'idle' },
        generate: { status: 'idle' },
        sql: null,
        explanation: null,
        result: { status: 'idle', outputs: [] },
        openStep: 'understand' as const,
      },
    ])
    void api.askNl2sql({ question, datasetIds }, {
      onPhase: (event) => updateTurn(turnId, (turn) => applyPhaseEvent(turn, event)),
      onSql: (sql, explanation) => updateTurn(turnId, (turn) => ({ ...turn, sql, explanation })),
      onDone: (result) => updateTurn(turnId, (turn) => ({
        ...turn,
        pending: false,
        openStep: 'result',
        sql: turn.sql ?? (result.status === 'success' ? result.sqlContent : null),
        explanation: turn.explanation ?? (result.status === 'success' ? result.explainContent : null),
        // 4 次重试耗尽走 done + failed：生成段若还停在 running 一并标错
        generate: result.status === 'failed' && turn.generate.status !== 'done'
          ? { ...turn.generate, status: 'error' }
          : turn.generate,
        result: {
          status: result.status === 'failed' ? 'error' : 'done',
          outputs: result.formatOutputs,
        },
        error: result.status === 'failed' ? result.error : turn.error,
      })),
      onError: (message) => updateTurn(turnId, (turn) => ({
        ...turn,
        pending: false,
        error: message,
        understand: turn.understand.status === 'running' ? { ...turn.understand, status: 'error' } : turn.understand,
        generate: turn.generate.status === 'running' ? { ...turn.generate, status: 'error' } : turn.generate,
        result: turn.result.status === 'running' ? { ...turn.result, status: 'error' } : turn.result,
      })),
    }).catch((error: Error) => {
      updateTurn(turnId, (turn) => (turn.pending ? { ...turn, pending: false, error: error.message } : turn))
    })
  }

  React.useLayoutEffect(() => {
    const element = scrollRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [turns])

  const copySql = (turnId: string, sql: string) => {
    void navigator.clipboard.writeText(sql).then(() => {
      setCopiedId(turnId)
      window.setTimeout(() => setCopiedId(null), 1500)
    })
  }

  const toggleStep = (turnId: string, step: Nl2sqlStep) => {
    updateTurn(turnId, (turn) => ({ ...turn, openStep: turn.openStep === step ? null : step }))
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={BarChart3} title="问数" subtitle="基于数据集的自然语言查数（NL2SQL）" />

      {/* 数据集选择栏：多选 chips；≥2 个走跨数据集复合流 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-6 py-3">
        <span className="text-sm text-zinc-500">数据集</span>
        {enabledDatasets.map((item) => {
          const checked = datasetIds.includes(item.id)
          return (
            <button
              key={item.id}
              type="button"
              title={item.description || item.name}
              onClick={() => toggleDataset(item.id)}
              className={cn(
                'flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm transition active:scale-[0.98]',
                checked
                  ? 'border-ink bg-ink text-white'
                  : 'border-line bg-panel text-zinc-600 hover:border-zinc-400',
              )}
            >
              {checked && <Check size={13} />}
              {item.name}
            </button>
          )
        })}
        {datasetIds.length > 1 && (
          <span className="text-xs text-zinc-400">已选 {datasetIds.length} 个，跨数据集联合问数</span>
        )}
        {datasetsQuery.isSuccess && enabledDatasets.length === 0 && (
          <span className="text-xs text-danger">暂无启用中的数据集，请先在「数据库管理」中启用</span>
        )}
      </div>

      {crossDatasource && (
        <div className="border-b border-line bg-amber-50 px-6 py-2 text-xs text-amber-700">
          跨数据集问数要求所选数据集挂在同一个数据源下，请调整选择。
        </div>
      )}
      {!crossDatasource && noPromptNames.length > 0 && (
        <div className="border-b border-line bg-amber-50 px-6 py-2 text-xs text-amber-700">
          {noPromptNames.map((name) => `「${name}」`).join('、')}
          缺提示词（治理状态「缺提示词」），问数效果可能受影响；可在「数据库管理 → 数据集」中补充。
        </div>
      )}

      {/* 对话区 */}
      <div ref={scrollRef} className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto max-w-4xl space-y-5">
          {turns.length === 0 && (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-400">
              {selectedDatasets.length > 0
                ? `向 ${selectedNames} 提问，如：各基金类别下收益为正的基金有多少只？`
                : '请先勾选数据集'}
            </div>
          )}
          {turns.map((turn) => (
            <div key={turn.id} className="space-y-3">
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-md bg-ink px-3.5 py-2.5 text-sm text-white">
                  {turn.question}
                </div>
              </div>
              <div className="space-y-2">
                {/* 段一：问题理解 */}
                <StepCard
                  title={STEP_TITLES.understand}
                  status={turn.understand.status}
                  open={turn.openStep === 'understand'}
                  onToggle={() => toggleStep(turn.id, 'understand')}
                >
                  {turn.understand.entityExplain && (
                    <p className="text-sm leading-6 text-zinc-700">{turn.understand.entityExplain}</p>
                  )}
                  {turn.understand.entities && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {[
                        ['时间', turn.understand.entities.time],
                        ['业务', turn.understand.entities.other],
                        ['指标', turn.understand.entities.metric],
                      ].map(([label, list]) =>
                        (list as string[] | undefined)?.map((entity) => (
                          <span key={`${label}-${entity}`} className="rounded border border-line bg-field px-2 py-0.5 text-xs text-zinc-600">
                            {label as string}·{entity}
                          </span>
                        )),
                      )}
                    </div>
                  )}
                  {turn.understand.candidates && Object.keys(turn.understand.candidates).length > 0 && (
                    <div className="mt-2 space-y-1">
                      {Object.entries(turn.understand.candidates).map(([entity, candidates]) => (
                        <div key={entity} className="text-xs text-zinc-500">
                          <span className="font-medium text-zinc-600">{entity}</span>
                          候选：
                          {candidates.slice(0, 5).map((candidate, index) => (
                            <span key={index} className="ml-1 rounded bg-field px-1.5 py-0.5 text-zinc-600">
                              {String(candidate.value ?? '')}
                            </span>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                  {turn.understand.status === 'running' && (
                    <p className="text-sm text-zinc-400">正在识别问题中的实体与时间范围…</p>
                  )}
                </StepCard>

                {/* 段二：查询生成 */}
                <StepCard
                  title={STEP_TITLES.generate}
                  status={turn.generate.status}
                  open={turn.openStep === 'generate'}
                  onToggle={() => toggleStep(turn.id, 'generate')}
                >
                  {turn.sql ? (
                    <div className="rounded-md border border-line bg-[#fafafa]">
                      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
                        <span className="text-xs font-semibold text-zinc-500">生成的 SQL</span>
                        <button
                          className="flex h-6 items-center gap-1 rounded px-1.5 text-xs text-zinc-500 hover:bg-field hover:text-ink"
                          onClick={() => copySql(turn.id, turn.sql!)}
                        >
                          {copiedId === turn.id ? <Check size={12} /> : <Copy size={12} />}
                          {copiedId === turn.id ? '已复制' : '复制'}
                        </button>
                      </div>
                      <pre className="thin-scrollbar overflow-x-auto px-3 py-2.5 font-mono text-xs leading-5 text-zinc-800">{turn.sql}</pre>
                    </div>
                  ) : (
                    <p className="text-sm text-zinc-400">
                      {turn.generate.status === 'running' ? '正在召回元数据并生成 SQL…' : '等待生成'}
                    </p>
                  )}
                  {turn.explanation && (
                    <p className="mt-2 text-xs leading-5 text-zinc-500">{turn.explanation}</p>
                  )}
                  {turn.generate.status === 'done' && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
                      {turn.generate.tables && turn.generate.tables.length > 0 && (
                        <span>用表 {turn.generate.tables.length} 张</span>
                      )}
                      {typeof turn.generate.rows === 'number' && <span>返回 {turn.generate.rows} 行</span>}
                      {typeof turn.generate.attempts === 'number' && turn.generate.attempts > 1 && (
                        <span>第 {turn.generate.attempts} 次尝试成功</span>
                      )}
                    </div>
                  )}
                </StepCard>

                {/* 段三：结果展示 */}
                <StepCard
                  title={STEP_TITLES.result}
                  status={turn.result.status}
                  open={turn.openStep === 'result'}
                  onToggle={() => toggleStep(turn.id, 'result')}
                >
                  {turn.error && turn.result.outputs.length === 0 ? (
                    <p className="text-sm text-danger">{turn.error}</p>
                  ) : turn.result.outputs.length > 0 ? (
                    turn.result.outputs.map((output, index) => (
                      <div key={index} className={cn(index > 0 && 'mt-4 border-t border-line pt-3')}>
                        {turn.result.outputs.length > 1 && (
                          <p className="mb-1 text-xs font-semibold text-zinc-500">{output.title}</p>
                        )}
                        <p className="text-sm leading-6 text-zinc-700">{output.resultDesc}</p>
                        {output.contentDesc && (
                          <p className="mt-1 text-sm leading-6 text-zinc-600">{output.contentDesc}</p>
                        )}
                        {output.figureType !== 'text' && output.dataFigure.length > 0 && (
                          <Nl2SqlChart output={output} />
                        )}
                        <ResultTable data={output.data} />
                        {output.chunkFlag && (
                          <p className="mt-1.5 text-xs text-amber-600">{output.chunkFlag}</p>
                        )}
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-zinc-400">
                      {turn.result.status === 'running' ? '正在整理查询结果…' : '等待结果'}
                    </p>
                  )}
                </StepCard>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 输入区 */}
      <div className="border-t border-line bg-[#fbfbfc] px-6 py-4">
        <div className="mx-auto max-w-4xl">
          <div className="rounded-md border border-line bg-panel shadow-sm">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  send()
                }
              }}
              className="block min-h-[72px] w-full resize-none bg-transparent px-4 py-3 text-sm outline-none"
              placeholder={
                selectedDatasets.length > 0
                  ? `向 ${selectedNames} 提问（Enter 发送，Shift+Enter 换行）`
                  : '请先勾选数据集'
              }
              disabled={selectedDatasets.length === 0}
            />
            <div className="flex items-center justify-end border-t border-line px-3 py-2">
              <button
                className={cn(
                  'flex h-8 items-center gap-2 rounded-md px-3 text-sm font-medium transition active:scale-[0.98]',
                  selectedDatasets.length > 0 && !crossDatasource && draft.trim()
                    ? 'bg-ink text-white'
                    : 'bg-zinc-200 text-zinc-400',
                )}
                disabled={
                  selectedDatasets.length === 0 ||
                  crossDatasource ||
                  !draft.trim() ||
                  turns.some((turn) => turn.pending)
                }
                onClick={send}
              >
                <Send size={15} />
                发送
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
