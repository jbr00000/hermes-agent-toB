import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Pencil, Plus, RefreshCw, RotateCcw, Search, SlidersHorizontal, Trash2 } from 'lucide-react'
import { api } from '../../api'
import type { Dataset, DatasetInput, DataSource } from '../../types'
import { Badge, DataTable, Td, Th, cn } from '../ui'
import { ConfirmDialog } from '../ConfirmDialog'
import { DatasetEditModal } from './DatasetEditModal'

const PAGE_SIZE = 10
const inputClass = 'h-9 w-full rounded-md border border-line bg-panel px-3 text-sm'

/** 治理状态（图3）：停用 > 缺提示词 > 缺业务说明 > 启用 */
type Governance = 'disabled' | 'missing_prompt' | 'missing_description' | 'enabled'

function governanceOf(dataset: Dataset): Governance {
  if (!dataset.enabled) return 'disabled'
  if (dataset.prompt.trim() === '') return 'missing_prompt'
  if (dataset.description.trim() === '') return 'missing_description'
  return 'enabled'
}

const GOVERNANCE_STYLES: Record<Governance, { label: string; className: string }> = {
  enabled: { label: '启用', className: 'bg-emerald-50 text-emerald-700' },
  missing_prompt: { label: '缺提示词', className: 'bg-amber-50 text-amber-700' },
  missing_description: { label: '缺业务说明', className: 'bg-amber-50 text-amber-700' },
  disabled: { label: '停用', className: 'bg-field text-zinc-500' },
}

/** 完整度（图3「全部完整度」筛选）：说明 + 提示词 + 至少一条表结构 DDL 才算完整 */
function isComplete(dataset: Dataset): boolean {
  return dataset.description.trim() !== '' && dataset.prompt.trim() !== '' && dataset.ddlCount > 0
}

interface Filters {
  keyword: string
  dataSourceId: string // '' = 全部数据源
  status: '' | 'enabled' | 'disabled'
  completeness: '' | 'complete' | 'incomplete'
}

const EMPTY_FILTERS: Filters = { keyword: '', dataSourceId: '', status: '', completeness: '' }

function applyFilters(datasets: Dataset[], filters: Filters): Dataset[] {
  const keyword = filters.keyword.trim().toLowerCase()
  return datasets.filter((dataset) => {
    if (keyword && !dataset.name.toLowerCase().includes(keyword) && !dataset.description.toLowerCase().includes(keyword)) return false
    if (filters.dataSourceId && dataset.dataSourceId !== filters.dataSourceId) return false
    if (filters.status === 'enabled' && !dataset.enabled) return false
    if (filters.status === 'disabled' && dataset.enabled) return false
    if (filters.completeness === 'complete' && !isComplete(dataset)) return false
    if (filters.completeness === 'incomplete' && isComplete(dataset)) return false
    return true
  })
}

/** 数据集管理列表（图3）：搜索 + 数据源/状态/完整度筛选 + 分页表格。
 *  筛选为「草稿 → 查询应用」两段式，对齐截图的 重置/查询/刷新 按钮。 */
export function DatasetListView({
  dataSources,
  onOpenMeta,
}: {
  dataSources: DataSource[]
  /** 跳转元数据配置页（图4，任务 #21） */
  onOpenMeta: (dataset: Dataset) => void
}) {
  const queryClient = useQueryClient()
  const listQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets })

  const [draft, setDraft] = React.useState<Filters>(EMPTY_FILTERS)
  const [applied, setApplied] = React.useState<Filters>(EMPTY_FILTERS)
  const [page, setPage] = React.useState(1)
  const [editTarget, setEditTarget] = React.useState<Dataset | null | 'create'>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<Dataset | null>(null)
  const [formError, setFormError] = React.useState<string | null>(null)

  const dataSourceNames = React.useMemo(
    () => new Map(dataSources.map((ds) => [ds.id, ds.name])),
    [dataSources],
  )

  const filtered = React.useMemo(() => applyFilters(listQuery.data ?? [], applied), [listQuery.data, applied])
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const paged = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['datasets'] })

  const saveMutation = useMutation({
    mutationFn: (input: DatasetInput) =>
      editTarget && editTarget !== 'create'
        ? api.updateDataset(editTarget.id, input)
        : api.createDataset(input),
    onSuccess: () => {
      setEditTarget(null)
      setFormError(null)
      invalidate()
    },
    onError: (err: Error) => setFormError(err.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteDataset(id),
    onSuccess: () => {
      setDeleteTarget(null)
      invalidate()
    },
    onError: (err: Error) => setFormError(err.message),
  })

  return (
    <div className="flex h-full flex-col">
      {/* 筛选栏（图3 顶部）：草稿状态，点「查询」才应用 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-6 py-3">
        <div className="relative w-56">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400" />
          <input
            className={cn(inputClass, 'pl-8')}
            placeholder="搜索数据集 / 说明"
            value={draft.keyword}
            onChange={(e) => setDraft({ ...draft, keyword: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setApplied(draft)
                setPage(1)
              }
            }}
          />
        </div>
        <select className={cn(inputClass, 'w-36')} value={draft.dataSourceId} onChange={(e) => setDraft({ ...draft, dataSourceId: e.target.value })}>
          <option value="">全部数据源</option>
          {dataSources.map((ds) => (
            <option key={ds.id} value={ds.id}>{ds.name}</option>
          ))}
        </select>
        <select className={cn(inputClass, 'w-28')} value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value as Filters['status'] })}>
          <option value="">全部状态</option>
          <option value="enabled">启用</option>
          <option value="disabled">停用</option>
        </select>
        <select className={cn(inputClass, 'w-32')} value={draft.completeness} onChange={(e) => setDraft({ ...draft, completeness: e.target.value as Filters['completeness'] })}>
          <option value="">全部完整度</option>
          <option value="complete">完整</option>
          <option value="incomplete">不完整</option>
        </select>

        <button
          className="flex h-9 items-center gap-1.5 rounded-md border border-line px-3 text-sm text-zinc-600 hover:bg-field"
          onClick={() => {
            setDraft(EMPTY_FILTERS)
            setApplied(EMPTY_FILTERS)
            setPage(1)
          }}
        >
          <RotateCcw size={14} />
          重置
        </button>
        <button
          className="flex h-9 items-center gap-1.5 rounded-md bg-ink px-3 text-sm font-medium text-white"
          onClick={() => {
            setApplied(draft)
            setPage(1)
          }}
        >
          <SlidersHorizontal size={14} />
          查询
        </button>
        <button
          className="flex h-9 items-center gap-1.5 rounded-md border border-line px-3 text-sm text-zinc-600 hover:bg-field"
          onClick={() => invalidate()}
        >
          <RefreshCw size={14} className={cn(listQuery.isFetching && 'animate-spin')} />
          刷新
        </button>

        <div className="ml-auto">
          <button
            className="flex h-9 items-center gap-1.5 rounded-md bg-ink px-3 text-sm font-medium text-white"
            onClick={() => {
              setFormError(null)
              setEditTarget('create')
            }}
          >
            <Plus size={15} />
            新增数据集
          </button>
        </div>
      </div>

      {/* 列表（图3 表格） */}
      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto">
        {listQuery.isLoading ? (
          <div className="space-y-3 p-6">
            {[0, 1, 2].map((item) => (
              <div key={item} className="h-14 animate-pulse rounded-md bg-field" />
            ))}
          </div>
        ) : paged.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-sm text-zinc-500">
            {applied === EMPTY_FILTERS ? '暂无数据集，点击右上角「新增数据集」创建' : '没有符合筛选条件的数据集'}
          </div>
        ) : (
          <DataTable>
            <thead>
              <tr>
                <Th className="w-[26%]">数据集 / 说明</Th>
                <Th className="w-[16%]">业务数据源（ID）</Th>
                <Th className="w-[10%]">DDL / 规则</Th>
                <Th className="w-[14%]">流程版本</Th>
                <Th className="w-[12%]">治理状态</Th>
                <Th className="w-[22%]">操作</Th>
              </tr>
            </thead>
            <tbody>
              {paged.map((dataset) => {
                const governance = GOVERNANCE_STYLES[governanceOf(dataset)]
                return (
                  <tr key={dataset.id} className="border-b border-line last:border-0">
                    <Td>
                      <div className="truncate font-medium text-ink">{dataset.name}</div>
                      <div className="mt-0.5 truncate text-xs text-zinc-500">{dataset.description || '（未填写业务说明）'}</div>
                    </Td>
                    <Td>
                      <div className="truncate">{dataSourceNames.get(dataset.dataSourceId) ?? '（数据源已删除）'}</div>
                      <div className="mt-0.5 truncate font-mono text-xs text-zinc-400">{dataset.dataSourceId}</div>
                    </Td>
                    <Td>
                      <span className="font-mono text-xs">{dataset.ddlCount} / {dataset.ruleCount}</span>
                    </Td>
                    <Td>
                      <Badge className="bg-field text-zinc-600">{dataset.flowVersion}</Badge>
                    </Td>
                    <Td>
                      <Badge className={governance.className}>{governance.label}</Badge>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-1">
                        <button
                          className="h-7 rounded-md px-2 text-xs text-zinc-600 hover:bg-field hover:text-ink"
                          onClick={() => onOpenMeta(dataset)}
                        >
                          元数据配置
                        </button>
                        <button
                          title="编辑"
                          className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-field hover:text-ink"
                          onClick={() => {
                            setFormError(null)
                            setEditTarget(dataset)
                          }}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          title="删除"
                          className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-red-50 hover:text-danger"
                          onClick={() => setDeleteTarget(dataset)}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </DataTable>
        )}
      </div>

      {/* 分页（图3 底部） */}
      {!listQuery.isLoading && filtered.length > 0 && (
        <footer className="flex items-center justify-between border-t border-line px-6 py-2">
          <span className="text-xs text-zinc-400">
            共 {filtered.length} 条 · 第 {currentPage} / {totalPages} 页
          </span>
          <div className="flex items-center gap-1">
            <button
              className="flex h-7 items-center gap-0.5 rounded border border-line px-2 text-xs text-zinc-600 hover:bg-field disabled:cursor-not-allowed disabled:opacity-40"
              disabled={currentPage <= 1}
              onClick={() => setPage(currentPage - 1)}
            >
              <ChevronLeft size={12} />
              上一页
            </button>
            <button
              className="flex h-7 items-center gap-0.5 rounded border border-line px-2 text-xs text-zinc-600 hover:bg-field disabled:cursor-not-allowed disabled:opacity-40"
              disabled={currentPage >= totalPages}
              onClick={() => setPage(currentPage + 1)}
            >
              下一页
              <ChevronRight size={12} />
            </button>
          </div>
        </footer>
      )}

      {editTarget !== null && (
        <DatasetEditModal
          target={editTarget === 'create' ? null : editTarget}
          dataSources={dataSources}
          pending={saveMutation.isPending}
          error={formError}
          onSubmit={(input) => saveMutation.mutate(input)}
          onClose={() => setEditTarget(null)}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title={`删除数据集：${deleteTarget.name}`}
          description={`将删除数据集「${deleteTarget.name}」及其元数据配置，不会改动数据源中的实际数据。`}
          pending={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  )
}
