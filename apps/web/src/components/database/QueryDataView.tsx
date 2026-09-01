import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { BarChart3, Check, Copy, Send } from 'lucide-react'
import { mockApi } from '../../mockApi'
import type { Nl2sqlAnswer } from '../../types'
import { PageHeader, cn } from '../ui'

interface Turn {
  id: string
  question: string
  answer: Nl2sqlAnswer | null
  error: string | null
  pending: boolean
}

const inputClass = 'h-9 rounded-md border border-line bg-panel px-3 text-sm'

/** 独立问数页（决策⑥）：选数据集 → 自然语言提问 → SQL + 结果集 + 小结。
 *  算法端接入前走 mockApi.askNl2sql（命中范例回金标准 SQL，否则模板 SQL）。 */
export function QueryDataView() {
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: mockApi.listDatasets })
  const enabledDatasets = (datasetsQuery.data ?? []).filter((item) => item.enabled)

  const [datasetId, setDatasetId] = React.useState('')
  const [draft, setDraft] = React.useState('')
  const [turns, setTurns] = React.useState<Turn[]>([])
  const [copiedId, setCopiedId] = React.useState<string | null>(null)
  const scrollRef = React.useRef<HTMLDivElement | null>(null)

  // 数据集清单加载后默认选第一个启用中的；切换数据集清空对话（后端语义按数据集隔离）
  React.useEffect(() => {
    if (!datasetId && enabledDatasets.length > 0) setDatasetId(enabledDatasets[0].id)
  }, [datasetId, enabledDatasets])

  const selectedDataset = enabledDatasets.find((item) => item.id === datasetId) ?? null

  const send = () => {
    const question = draft.trim()
    if (!question || !datasetId) return
    const turnId = `turn-${Date.now()}`
    setDraft('')
    setTurns((current) => [...current, { id: turnId, question, answer: null, error: null, pending: true }])
    mockApi.askNl2sql(datasetId, question)
      .then((answer) => {
        setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, answer, pending: false } : turn))
      })
      .catch((error: Error) => {
        setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, error: error.message, pending: false } : turn))
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

  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={BarChart3} title="问数" subtitle="基于数据集的自然语言查数（NL2SQL）· 算法端接入前为前端演示数据" />

      {/* 数据集选择栏 */}
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-3">
        <span className="text-sm text-zinc-500">数据集</span>
        <select
          className={cn(inputClass, 'w-64')}
          value={datasetId}
          onChange={(e) => {
            setDatasetId(e.target.value)
            setTurns([])
          }}
        >
          {enabledDatasets.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
        {selectedDataset && (
          <span className="text-xs text-zinc-400">{selectedDataset.description}</span>
        )}
        {datasetsQuery.isSuccess && enabledDatasets.length === 0 && (
          <span className="text-xs text-danger">暂无启用中的数据集，请先在「数据库管理」中启用</span>
        )}
      </div>

      {selectedDataset && selectedDataset.prompt.trim() === '' && (
        <div className="border-b border-line bg-amber-50 px-6 py-2 text-xs text-amber-700">
          该数据集缺提示词（治理状态「缺提示词」），问数效果可能受影响；可在「数据库管理 → 数据集」中补充。
        </div>
      )}

      {/* 对话区 */}
      <div ref={scrollRef} className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto max-w-4xl space-y-5">
          {turns.length === 0 && (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-400">
              {selectedDataset
                ? `向「${selectedDataset.name}」提问，如：各基金类别下收益为正的基金有多少只？`
                : '请先选择数据集'}
            </div>
          )}
          {turns.map((turn) => (
            <div key={turn.id} className="space-y-3">
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-md bg-ink px-3.5 py-2.5 text-sm text-white">
                  {turn.question}
                </div>
              </div>
              <div className="rounded-md border border-line bg-panel p-4">
                {turn.pending ? (
                  <div className="flex items-center gap-2 text-sm text-zinc-500">
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-300 border-t-ink" />
                    正在生成 SQL 并查询…
                  </div>
                ) : turn.error ? (
                  <div className="text-sm text-danger">{turn.error}</div>
                ) : turn.answer ? (
                  <>
                    <p className="text-sm leading-6 text-zinc-700">{turn.answer.summary}</p>
                    <div className="mt-3 rounded-md border border-line bg-[#fafafa]">
                      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
                        <span className="text-xs font-semibold text-zinc-500">生成的 SQL</span>
                        <button
                          className="flex h-6 items-center gap-1 rounded px-1.5 text-xs text-zinc-500 hover:bg-field hover:text-ink"
                          onClick={() => copySql(turn.id, turn.answer!.sql)}
                        >
                          {copiedId === turn.id ? <Check size={12} /> : <Copy size={12} />}
                          {copiedId === turn.id ? '已复制' : '复制'}
                        </button>
                      </div>
                      <pre className="thin-scrollbar overflow-x-auto px-3 py-2.5 font-mono text-xs leading-5 text-zinc-800">{turn.answer.sql}</pre>
                    </div>
                    <div className="thin-scrollbar mt-3 overflow-x-auto rounded-md border border-line">
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr>
                            {turn.answer.columns.map((column) => (
                              <th key={column} className="border-b border-line bg-[#fafafa] px-3 py-2 text-xs font-semibold text-zinc-500">{column}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {turn.answer.rows.map((row, rowIndex) => (
                            <tr key={rowIndex} className="border-b border-line last:border-0">
                              {row.map((cell, cellIndex) => (
                                <td key={cellIndex} className="px-3 py-2 text-sm text-zinc-700">{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="mt-2 text-right text-xs text-zinc-400">耗时 {turn.answer.durationMs}ms · 演示数据</div>
                  </>
                ) : null}
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
              placeholder={selectedDataset ? `向「${selectedDataset.name}」提问（Enter 发送，Shift+Enter 换行）` : '请先选择数据集'}
              disabled={!selectedDataset}
            />
            <div className="flex items-center justify-end border-t border-line px-3 py-2">
              <button
                className={cn(
                  'flex h-8 items-center gap-2 rounded-md px-3 text-sm font-medium transition active:scale-[0.98]',
                  selectedDataset && draft.trim() ? 'bg-ink text-white' : 'bg-zinc-200 text-zinc-400',
                )}
                disabled={!selectedDataset || !draft.trim() || turns.some((turn) => turn.pending)}
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
