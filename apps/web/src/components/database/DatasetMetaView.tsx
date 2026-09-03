import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  DatabaseZap,
  Download,
  Eraser,
  FileUp,
  History,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
  TableProperties,
  Trash2,
  Workflow,
  type LucideIcon,
} from 'lucide-react'
import { api } from '../../api'
import type { MetaImportPreview, MetaKind, Nl2sqlSyncRecord } from '../../types'
import { Badge, DataTable, PageHeader, Td, Th, Toggle, cn, formatDateTime } from '../ui'
import { ConfirmDialog } from '../ConfirmDialog'

type MetaRow = Record<string, unknown> & { id: string; updatedAt: number }

interface ColumnDef {
  key: string
  label: string
  mono?: boolean
  className?: string
  render?: (row: MetaRow) => React.ReactNode
}

interface FieldDef {
  key: string
  label: string
  textarea?: boolean
  /** 非 textarea 字段默认必填；textarea 可选填（对齐 Excel 模板只校验关键列的口径） */
  required?: boolean
  placeholder?: string
}

interface TabDef {
  label: string
  addLabel: string
  columns: ColumnDef[]
  fields: FieldDef[]
}

const PROVIDER_BADGE: Record<string, { label: string; className: string }> = {
  MANUAL: { label: '人工', className: 'bg-field text-zinc-500' },
  AI: { label: 'AI', className: 'bg-indigo-50 text-indigo-600' },
}

/** 行内「来源」徽章（MANUAL=人工 / AI=自动抓取生成） */
function ProviderBadge({ provider }: { provider: unknown }) {
  const style = PROVIDER_BADGE[String(provider ?? 'MANUAL')] ?? PROVIDER_BADGE.MANUAL
  return <Badge className={style.className}>{style.label}</Badge>
}

const TAB_DEFS: Record<MetaKind, TabDef> = {
  tables: {
    label: '表结构',
    addLabel: '新增表结构',
    columns: [
      {
        key: 'tableName',
        label: '表名 / 说明',
        className: 'w-[40%]',
        render: (row) => (
          <>
            <div className="truncate font-mono text-[13px] font-medium text-ink">{String(row.tableName ?? '')}</div>
            <div className="mt-0.5 truncate text-xs text-zinc-500">{String(row.description ?? '') || '（未填写说明）'}</div>
          </>
        ),
      },
    ],
    fields: [
      { key: 'tableName', label: '表名', required: true, placeholder: '如：mf_fundarchives' },
      { key: 'ddlContent', label: '建表语句（DDL）', textarea: true, required: true, placeholder: '完整 CREATE TABLE 语句，将原文注入 SQL 生成 prompt' },
      { key: 'description', label: '说明', textarea: true, placeholder: '表的业务含义（取自数据源注释或手工补充）' },
    ],
  },
  terms: {
    label: '术语',
    addLabel: '新增术语',
    columns: [
      { key: 'term', label: '术语', className: 'w-[20%]' },
      { key: 'definition', label: '术语解释' },
      { key: 'synonyms', label: '近义词', className: 'w-[18%]' },
    ],
    fields: [
      { key: 'term', label: '术语名', required: true, placeholder: '如：基金收益为正' },
      { key: 'definition', label: '术语解释', textarea: true, placeholder: '业务口径或对应的字段表达式' },
      { key: 'synonyms', label: '近义词（顿号/逗号分隔）', placeholder: '参与检索召回，如：正收益、收益为正' },
      { key: 'remark', label: '备注', textarea: true },
    ],
  },
  metrics: {
    label: '指标',
    addLabel: '新增指标',
    columns: [
      { key: 'name', label: '指标名', className: 'w-[18%]' },
      { key: 'displayName', label: '展示名', className: 'w-[14%]' },
      { key: 'expression', label: '计算口径', mono: true },
    ],
    fields: [
      { key: 'name', label: '指标名', required: true, placeholder: '如：正收益基金数' },
      { key: 'displayName', label: '指标展示名', placeholder: '结果展示用的中文名' },
      { key: 'expression', label: '计算口径', textarea: true, placeholder: '如：COUNT(*) WHERE fundreturn > 0' },
      { key: 'remark', label: '备注', textarea: true },
    ],
  },
  dimensions: {
    label: '维度',
    addLabel: '新增维度',
    columns: [
      { key: 'name', label: '维度名', mono: true, className: 'w-[26%]' },
      { key: 'dataKey', label: '标准值Key', mono: true, className: 'w-[22%]' },
      { key: 'dataValue', label: '标准值', className: 'w-[22%]' },
    ],
    fields: [
      { key: 'name', label: '维度名（表名.字段名）', required: true, placeholder: '如：mf_fundreturnrank.fundtypename' },
      { key: 'displayName', label: '维度展示名', placeholder: '如：基金类别' },
      { key: 'dataKey', label: '标准值Key（库存编码）', required: true, placeholder: '数据库中实际存储的编码' },
      { key: 'dataValue', label: '标准值（中文）', required: true, placeholder: '用户口中的标准中文说法' },
      { key: 'remark', label: '备注', textarea: true },
    ],
  },
  foreignKeys: {
    label: '外键关系',
    addLabel: '新增外键关系',
    columns: [
      { key: 'fromTable', label: '源表', mono: true, className: 'w-[18%]' },
      { key: 'fromColumn', label: '源表字段', mono: true, className: 'w-[14%]' },
      { key: 'toTable', label: '目标表', mono: true, className: 'w-[18%]' },
      { key: 'toColumn', label: '目标表字段', mono: true, className: 'w-[14%]' },
      { key: 'relationDesc', label: '关联说明' },
    ],
    fields: [
      { key: 'fromTable', label: '源表', required: true, placeholder: '如：mf_fundarchives' },
      { key: 'fromColumn', label: '源表字段', required: true, placeholder: '如：InnerCode' },
      { key: 'toTable', label: '目标表', required: true, placeholder: '如：mf_assetallocation' },
      { key: 'toColumn', label: '目标表字段', required: true, placeholder: '如：InnerCode' },
      { key: 'relationDesc', label: '关联说明', textarea: true, placeholder: 'join 的业务含义（注入 SQL 生成 prompt）' },
    ],
  },
  examples: {
    label: '范例',
    addLabel: '新增范例',
    columns: [
      { key: 'question', label: '问题', className: 'w-[44%]' },
      { key: 'sql', label: '参考 SQL', mono: true },
    ],
    fields: [
      { key: 'question', label: '问题', textarea: true, required: true, placeholder: '用户的自然语言问题' },
      { key: 'sql', label: '参考 SQL', textarea: true, required: true, placeholder: '该问题的金标准 SQL（few-shot 示例）' },
      { key: 'remark', label: '备注', textarea: true },
    ],
  },
}

const TAB_ORDER: MetaKind[] = ['tables', 'terms', 'metrics', 'dimensions', 'foreignKeys', 'examples']

const inputClass = 'h-9 w-full rounded-md border border-line bg-panel px-3 text-sm'
const textareaClass = 'w-full rounded-md border border-line bg-panel px-3 py-2 text-sm'
const actionBtnClass = 'flex h-8 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs text-zinc-600 hover:bg-field hover:text-ink'

/** 元数据配置页（图4）：左侧数据集列表 + 右侧六类元数据 tab。
 *  下载模板/导出/导入Excel/从数据源同步DDL/三端重同步（ES/Milvus 重建）/同步历史
 *  均走后端 /nl2sql/* 真实接口。 */
export function DatasetMetaView({ datasetId }: { datasetId: string }) {
  const queryClient = useQueryClient()
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets })
  const dataSourcesQuery = useQuery({ queryKey: ['dataSources'], queryFn: api.listDataSources })

  const [selectedId, setSelectedId] = React.useState(datasetId)
  const [tab, setTab] = React.useState<MetaKind>('tables')
  const [editTarget, setEditTarget] = React.useState<MetaRow | 'create' | null>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<MetaRow | null>(null)
  const [clearConfirm, setClearConfirm] = React.useState(false)
  const [resyncConfirm, setResyncConfirm] = React.useState(false)
  const [pendingSyncId, setPendingSyncId] = React.useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = React.useState(false)
  const [importPreview, setImportPreview] = React.useState<MetaImportPreview | null>(null)
  const [importing, setImporting] = React.useState(false)
  const [toast, setToast] = React.useState<string | null>(null)
  const fileInputRef = React.useRef<HTMLInputElement | null>(null)

  React.useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const metaQuery = useQuery({
    queryKey: ['datasetMeta', selectedId],
    queryFn: () => api.getDatasetMeta(selectedId),
  })

  const historyQuery = useQuery({
    queryKey: ['nl2sqlSyncHistory', selectedId],
    queryFn: () => api.getNl2sqlSyncHistory(selectedId),
    enabled: historyOpen || pendingSyncId != null,
    // 有记录处于 running/pending 时轮询（重同步在服务端后台线程执行）
    refetchInterval: (query) =>
      (query.state.data ?? []).some((record) => record.overallStatus === 'running' || record.overallStatus === 'pending')
        ? 2500
        : false,
  })

  // 重同步完成后 toast 结果（只针对本次触发的那条记录）
  React.useEffect(() => {
    if (!pendingSyncId) return
    const record = historyQuery.data?.find((item) => item.id === pendingSyncId)
    if (!record || record.overallStatus === 'running' || record.overallStatus === 'pending') return
    setPendingSyncId(null)
    setToast(record.overallStatus === 'success'
      ? '三端重同步完成：五段资产已全部写入 ES / Milvus'
      : `三端重同步未完成：${record.overallMessage ?? '部分资产同步失败（详见同步历史）'}`)
  }, [pendingSyncId, historyQuery.data])

  const datasets = datasetsQuery.data ?? []
  const dataset = datasets.find((item) => item.id === selectedId) ?? null
  const dataSourceName = dataset
    ? dataSourcesQuery.data?.find((ds) => ds.id === dataset.dataSourceId)?.name ?? '（数据源已删除）'
    : ''
  const bundle = metaQuery.data
  const rows = (bundle?.[tab] ?? []) as unknown as MetaRow[]
  const tabDef = TAB_DEFS[tab]

  const invalidateMeta = () => {
    void queryClient.invalidateQueries({ queryKey: ['datasetMeta', selectedId] })
    void queryClient.invalidateQueries({ queryKey: ['datasets'] }) // ddlCount 联动
  }

  const saveMutation = useMutation({
    mutationFn: (values: Record<string, unknown> & { id?: string }) => api.saveMetaItem(selectedId, tab, values),
    onSuccess: () => {
      setEditTarget(null)
      invalidateMeta()
    },
    onError: (err: Error) => setToast(err.message),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteMetaItem(selectedId, tab, id),
    onSuccess: () => {
      setDeleteTarget(null)
      invalidateMeta()
    },
    onError: (err: Error) => setToast(err.message),
  })
  const clearMutation = useMutation({
    mutationFn: () => api.clearDatasetMeta(selectedId),
    onSuccess: () => {
      setClearConfirm(false)
      invalidateMeta()
      setToast('元数据已清空')
    },
    onError: (err: Error) => setToast(err.message),
  })
  const toggleTableMutation = useMutation({
    mutationFn: (row: MetaRow) => api.saveMetaItem(selectedId, 'tables', { id: row.id, enabled: !row.enabled }),
    onSuccess: () => invalidateMeta(),
    onError: (err: Error) => setToast(err.message),
  })
  const syncDdlMutation = useMutation({
    mutationFn: () => api.syncDdlFromDataSource(selectedId),
    onSuccess: (result) => {
      invalidateMeta()
      setToast(`已从数据源同步 DDL：共 ${result.total} 张表，新增 ${result.created}、更新 ${result.updated}`)
    },
    onError: (err: Error) => setToast(`从数据源同步 DDL 失败：${err.message}`),
  })
  const resyncMutation = useMutation({
    mutationFn: () => api.triggerNl2sqlSync(selectedId),
    onSuccess: (record) => {
      setResyncConfirm(false)
      setPendingSyncId(record.id)
      setToast('三端重同步已开始，正在后台写入 ES / Milvus…')
    },
    onError: (err: Error) => {
      setResyncConfirm(false)
      setToast(`三端重同步触发失败：${err.message}`)
    },
  })
  const importConfirmMutation = useMutation({
    mutationFn: (previewId: string) => api.importMetaConfirm(selectedId, previewId),
    onSuccess: (result) => {
      setImportPreview(null)
      invalidateMeta()
      setToast(`导入完成：新增 ${result.created} 条、更新 ${result.updated} 条`)
    },
    onError: (err: Error) => setToast(`导入失败：${err.message}`),
  })

  const runDownload = (label: string, task: () => Promise<void>) => {
    task().catch((err: Error) => setToast(`${label}失败：${err.message}`))
  }

  /** Excel 导入第一步：选文件 → 上传解析出预览（不改变库） */
  const onImportFilePicked = (file: File) => {
    setImporting(true)
    api.importMetaPreview(selectedId, file)
      .then((preview) => setImportPreview(preview))
      .catch((err: Error) => setToast(`Excel 解析失败：${err.message}`))
      .finally(() => setImporting(false))
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={TableProperties} title="元数据配置" subtitle={dataset ? `${dataset.name} · 供问数（NL2SQL）流程使用的语义层` : '数据集元数据'} />

      <div className="flex min-h-0 flex-1">
        {/* 左：数据集列表 */}
        <aside className="thin-scrollbar w-52 shrink-0 overflow-y-auto border-r border-line py-2">
          {datasets.map((item) => (
            <button
              key={item.id}
              className={cn(
                'w-full px-4 py-2.5 text-left transition hover:bg-field',
                item.id === selectedId && 'bg-field',
              )}
              onClick={() => setSelectedId(item.id)}
            >
              <div className={cn('truncate text-sm', item.id === selectedId ? 'font-semibold text-ink' : 'text-zinc-700')}>
                {item.name}
              </div>
              <div className="mt-0.5 text-xs text-zinc-400">{item.enabled ? '已启用' : '已停用'} · DDL {item.ddlCount}</div>
            </button>
          ))}
          {datasetsQuery.isLoading && <div className="px-4 py-3 text-xs text-zinc-400">加载中…</div>}
        </aside>

        {/* 右：元数据明细 */}
        <main className="flex min-w-0 flex-1 flex-col">
          {/* 顶部：徽章 + 操作（图4 顶栏） */}
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
            <Badge className="bg-field text-zinc-600" title="业务数据源">
              数据源：{dataSourceName}
            </Badge>
            {dataset && (
              <Badge className={dataset.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-field text-zinc-500'}>
                {dataset.enabled ? '已启用' : '已停用'}
              </Badge>
            )}
            <button className={actionBtnClass} onClick={() => setHistoryOpen(true)}>
              <History size={13} />
              三端同步历史
            </button>
            <button
              className={actionBtnClass}
              disabled={pendingSyncId != null || resyncMutation.isPending}
              onClick={() => setResyncConfirm(true)}
            >
              {pendingSyncId != null ? <LoaderCircle size={13} className="animate-spin" /> : <Workflow size={13} />}
              {pendingSyncId != null ? '同步中…' : '三端重同步'}
            </button>

            <div className="ml-auto flex items-center gap-1.5">
              <button className={actionBtnClass} onClick={() => metaQuery.refetch()}>
                <RefreshCw size={13} className={cn(metaQuery.isFetching && 'animate-spin')} />
                刷新
              </button>
              <button
                className={actionBtnClass}
                onClick={() => runDownload('下载模板', () => api.downloadMetaTemplate(selectedId, `元数据配置模板-${dataset?.name ?? ''}.xlsx`))}
              >
                <Download size={13} />
                下载模板
              </button>
              <button
                className={actionBtnClass}
                onClick={() => runDownload('导出数据', () => api.exportDatasetMeta(selectedId, `元数据配置导出数据-${dataset?.name ?? ''}.xlsx`))}
              >
                <FileUp size={13} />
                导出数据
              </button>
              <button
                className="flex h-8 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs text-danger hover:bg-red-50"
                onClick={() => setClearConfirm(true)}
              >
                <Eraser size={13} />
                清空元数据
              </button>
              <button className={actionBtnClass} disabled={importing} onClick={() => fileInputRef.current?.click()}>
                {importing ? <LoaderCircle size={13} className="animate-spin" /> : <FileUp size={13} />}
                导入Excel
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  event.target.value = '' // 允许重复选同一文件
                  if (file) onImportFilePicked(file)
                }}
              />
            </div>
          </div>

          {/* 六类元数据 tab */}
          <div className="flex items-center gap-1 border-b border-line px-5 py-2">
            {TAB_ORDER.map((kind) => (
              <button
                key={kind}
                className={cn(
                  'h-8 rounded-md px-3 text-sm transition',
                  tab === kind ? 'bg-ink font-medium text-white' : 'text-zinc-600 hover:bg-field',
                )}
                onClick={() => setTab(kind)}
              >
                {TAB_DEFS[kind].label}
                <span className="ml-1 text-xs opacity-70">{bundle?.[kind].length ?? 0}</span>
              </button>
            ))}
          </div>

          {/* tab 工具栏 */}
          <div className="flex items-center justify-between border-b border-line px-5 py-2.5">
            <div className="text-xs text-zinc-500">
              {tab === 'tables' ? '表结构是问数流程的 schema 来源；停用的表不下发给模型' : `${tabDef.label}会作为语义层提示注入问数流程`}
            </div>
            <div className="flex items-center gap-1.5">
              {tab === 'tables' && (
                <button
                  className={actionBtnClass}
                  disabled={syncDdlMutation.isPending}
                  onClick={() => syncDdlMutation.mutate()}
                >
                  {syncDdlMutation.isPending ? <LoaderCircle size={13} className="animate-spin" /> : <DatabaseZap size={13} />}
                  从数据源同步 DDL
                </button>
              )}
              <button
                className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-2.5 text-xs font-medium text-white"
                onClick={() => setEditTarget('create')}
              >
                <Plus size={13} />
                {tabDef.addLabel}
              </button>
            </div>
          </div>

          {/* 列表 */}
          <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto">
            {metaQuery.isLoading ? (
              <div className="space-y-3 p-5">
                {[0, 1, 2].map((item) => (
                  <div key={item} className="h-12 animate-pulse rounded-md bg-field" />
                ))}
              </div>
            ) : rows.length === 0 ? (
              <div className="flex h-40 items-center justify-center text-sm text-zinc-500">
                暂无{tabDef.label}，点击右上角「{tabDef.addLabel}」添加
              </div>
            ) : (
              <DataTable>
                <thead>
                  <tr>
                    {tabDef.columns.map((col) => (
                      <Th key={col.key} className={col.className}>{col.label}</Th>
                    ))}
                    <Th className="w-[7%]">来源</Th>
                    {tab === 'tables' && <Th className="w-[10%]">状态</Th>}
                    <Th className="w-[11%]">更新时间</Th>
                    <Th className="w-[10%]">操作</Th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className="border-b border-line last:border-0">
                      {tabDef.columns.map((col) => (
                        <Td key={col.key} className={col.mono ? 'font-mono text-xs' : undefined}>
                          {col.render ? col.render(row) : (
                            <span className="block truncate">{String(row[col.key] ?? '')}</span>
                          )}
                        </Td>
                      ))}
                      <Td>
                        <ProviderBadge provider={row.provider} />
                      </Td>
                      {tab === 'tables' && (
                        <Td>
                          <div className="flex items-center gap-2">
                            <Toggle
                              checked={Boolean(row.enabled)}
                              disabled={toggleTableMutation.isPending}
                              onChange={() => toggleTableMutation.mutate(row)}
                              label={row.enabled ? `停用 ${String(row.tableName ?? '')}` : `启用 ${String(row.tableName ?? '')}`}
                            />
                            <span className="text-xs text-zinc-500">{row.enabled ? '启用中' : '已停用'}</span>
                          </div>
                        </Td>
                      )}
                      <Td>
                        <span className="text-xs text-zinc-500">{formatDateTime(Number(row.updatedAt) * 1000)}</span>
                      </Td>
                      <Td>
                        <div className="flex items-center gap-1">
                          <button
                            title="编辑"
                            className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-field hover:text-ink"
                            onClick={() => setEditTarget(row)}
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            title="删除"
                            className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-red-50 hover:text-danger"
                            onClick={() => setDeleteTarget(row)}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            )}
          </div>
        </main>
      </div>

      {editTarget !== null && (
        <MetaEditModal
          kind={tab}
          target={editTarget === 'create' ? null : editTarget}
          pending={saveMutation.isPending}
          onSubmit={(values) => saveMutation.mutate(values)}
          onClose={() => setEditTarget(null)}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title={`删除${tabDef.label}`}
          description="删除后该条元数据不再注入问数流程，此操作不可恢复。"
          pending={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {clearConfirm && (
        <ConfirmDialog
          icon={Eraser}
          title="清空元数据"
          description={`将清空「${dataset?.name ?? ''}」的全部六类元数据（表结构/术语/指标/维度/外键关系/范例），此操作不可恢复。`}
          confirmLabel="清空"
          pending={clearMutation.isPending}
          onConfirm={() => clearMutation.mutate()}
          onCancel={() => setClearConfirm(false)}
        />
      )}

      {resyncConfirm && (
        <ConfirmDialog
          icon={Workflow}
          title="三端重同步"
          description="系统将基于当前 MySQL 元数据，清理 ES 和 Milvus 中该数据集的同步内容，再按当前数据重新构建检索数据。同步在后台执行，期间可以离开本页。"
          confirmLabel="开始重同步"
          pending={resyncMutation.isPending}
          onConfirm={() => resyncMutation.mutate()}
          onCancel={() => setResyncConfirm(false)}
        />
      )}

      {historyOpen && (
        <SimpleModal icon={History} title="三端同步历史" onClose={() => setHistoryOpen(false)}>
          {historyQuery.isLoading ? (
            <div className="flex h-28 items-center justify-center text-sm text-zinc-400">加载中…</div>
          ) : (historyQuery.data ?? []).length === 0 ? (
            <div className="flex h-28 items-center justify-center text-sm text-zinc-500">
              暂无同步记录，点击「三端重同步」发起首次同步
            </div>
          ) : (
            <div className="thin-scrollbar -mx-1 max-h-80 space-y-2 overflow-y-auto px-1">
              {(historyQuery.data ?? []).map((record) => (
                <SyncHistoryItem key={record.id} record={record} />
              ))}
            </div>
          )}
        </SimpleModal>
      )}

      {importPreview && (
        <ImportPreviewModal
          preview={importPreview}
          pending={importConfirmMutation.isPending}
          onConfirm={() => importConfirmMutation.mutate(importPreview.previewId)}
          onClose={() => setImportPreview(null)}
        />
      )}

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-md border border-line bg-panel px-4 py-2 text-sm shadow-card">
          {toast}
        </div>
      )}
    </div>
  )
}

function SimpleModal({
  icon: Icon,
  title,
  children,
  onClose,
}: {
  icon: LucideIcon
  title: string
  children: React.ReactNode
  onClose: () => void
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="w-full max-w-md rounded-md border border-line bg-panel p-5 shadow-card" role="dialog" aria-modal="true">
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-field text-zinc-700">
            <Icon size={17} />
          </div>
          <div className="text-sm font-semibold">{title}</div>
        </div>
        {children}
        <div className="mt-4 flex justify-end">
          <button className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}

const TRIGGER_TYPE_LABELS: Record<string, string> = {
  ASSET_CHANGE: '资产变更',
  MANUAL_RESYNC: '手动重同步',
  CLEAR: '清空',
  IMPORT: 'Excel 导入',
}

const SYNC_STATUS_STYLES: Record<string, { label: string; className: string }> = {
  success: { label: '成功', className: 'bg-emerald-50 text-emerald-700' },
  failed: { label: '失败', className: 'bg-red-50 text-danger' },
  running: { label: '进行中', className: 'bg-amber-50 text-amber-700' },
  pending: { label: '待执行', className: 'bg-field text-zinc-500' },
  skipped: { label: '跳过', className: 'bg-field text-zinc-400' },
}

/** 三端同步历史的一项：整体状态 + 五段资产各自的状态 */
function SyncHistoryItem({ record }: { record: Nl2sqlSyncRecord }) {
  const overall = SYNC_STATUS_STYLES[record.overallStatus] ?? SYNC_STATUS_STYLES.pending
  return (
    <div className="rounded-md border border-line px-3 py-2">
      <div className="flex items-center gap-2">
        <Badge className="bg-field text-zinc-600">{TRIGGER_TYPE_LABELS[record.triggerType] ?? record.triggerType}</Badge>
        <Badge className={overall.className}>{overall.label}</Badge>
        <span className="ml-auto text-xs text-zinc-400">{formatDateTime(record.createdAt * 1000)}</span>
      </div>
      {record.overallMessage && <div className="mt-1 text-xs text-zinc-500">{record.overallMessage}</div>}
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-500">
        {record.segments.map((segment) => {
          const style = SYNC_STATUS_STYLES[segment.status] ?? SYNC_STATUS_STYLES.pending
          return (
            <span key={segment.key} title={segment.message ?? undefined}>
              {segment.label}：<span className={cn('font-medium', style.className.replace('bg-', 'text-').split(' ')[1] ?? '')}>{style.label}</span>
            </span>
          )
        })}
      </div>
    </div>
  )
}

/** Excel 导入预览（两步导入的第一步结果）：按类型分组的新增/更新/重复统计 + 行级错误，确认后落库 */
function ImportPreviewModal({
  preview,
  pending,
  onConfirm,
  onClose,
}: {
  preview: MetaImportPreview
  pending: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  const totalCreate = Object.values(preview.typeSummaries).reduce((sum, item) => sum + (item?.create ?? 0), 0)
  const totalUpdate = Object.values(preview.typeSummaries).reduce((sum, item) => sum + (item?.update ?? 0), 0)
  const hasChanges = totalCreate + totalUpdate > 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onClose()
      }}
    >
      <div className="w-full max-w-lg rounded-md border border-line bg-panel p-5 shadow-card" role="dialog" aria-modal="true">
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-field text-zinc-700">
            <FileUp size={17} />
          </div>
          <div className="text-sm font-semibold">导入预览</div>
        </div>

        <div className="space-y-1.5 text-sm">
          {TAB_ORDER.map((kind) => {
            const summary = preview.typeSummaries[kind]
            if (!summary || summary.read === 0) return null
            return (
              <div key={kind} className="flex items-center justify-between rounded-md bg-field px-3 py-1.5">
                <span>{TAB_DEFS[kind].label}</span>
                <span className="text-xs text-zinc-500">
                  读取 {summary.read} · <span className="text-emerald-700">新增 {summary.create}</span>
                  {' · '}<span className="text-amber-700">更新 {summary.update}</span>
                  {' · '}重复跳过 {summary.duplicate}
                  {summary.error > 0 && <span className="text-danger"> · 错误 {summary.error}</span>}
                </span>
              </div>
            )
          })}
        </div>

        {preview.errors.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-xs font-semibold text-danger">行级错误（这些行不会被导入）</div>
            <div className="thin-scrollbar max-h-32 space-y-1 overflow-y-auto rounded-md border border-red-100 bg-red-50/50 px-3 py-2 text-xs text-zinc-600">
              {preview.errors.map((error, index) => (
                <div key={index}>{error.sheet} 第 {error.row} 行：{error.message}</div>
              ))}
            </div>
          </div>
        )}

        {preview.ignoredSheets.length > 0 && (
          <div className="mt-2 text-xs text-zinc-400">已忽略 sheet：{preview.ignoredSheets.join('、')}</div>
        )}

        {!hasChanges && preview.errors.length === 0 && (
          <div className="mt-3 text-xs text-zinc-500">文件内容与当前元数据完全一致，无需导入。</div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field disabled:text-zinc-400" disabled={pending} onClick={onClose}>
            取消
          </button>
          <button
            className="flex h-9 items-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white disabled:bg-zinc-300"
            disabled={pending || !hasChanges}
            onClick={onConfirm}
          >
            {pending && <LoaderCircle size={15} className="animate-spin" />}
            确认导入（新增 {totalCreate} / 更新 {totalUpdate}）
          </button>
        </div>
      </div>
    </div>
  )
}

/** 六类元数据共用的新增/编辑弹窗（字段由 TAB_DEFS[kind].fields 驱动） */
function MetaEditModal({
  kind,
  target,
  pending,
  onSubmit,
  onClose,
}: {
  kind: MetaKind
  target: MetaRow | null
  pending: boolean
  onSubmit: (values: Record<string, unknown> & { id?: string }) => void
  onClose: () => void
}) {
  const def = TAB_DEFS[kind]
  const editing = target !== null
  const [values, setValues] = React.useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    for (const field of def.fields) initial[field.key] = target ? String(target[field.key] ?? '') : ''
    return initial
  })
  const [enabled, setEnabled] = React.useState(target ? Boolean(target.enabled) : true)

  const valid = def.fields.every((field) => !field.required || values[field.key].trim() !== '')
  const title = `${editing ? '编辑' : '新增'}${def.label}`

  const submit = () => {
    if (!valid || pending) return
    const payload: Record<string, unknown> & { id?: string } = {}
    for (const field of def.fields) payload[field.key] = values[field.key].trim()
    if (editing) payload.id = target.id
    if (kind === 'tables') payload.enabled = enabled
    onSubmit(payload)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onClose()
      }}
    >
      <div className="w-full max-w-md rounded-md border border-line bg-panel p-5 shadow-card" role="dialog" aria-modal="true">
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-field text-zinc-700">
            {editing ? <Pencil size={17} /> : <Plus size={17} />}
          </div>
          <div className="text-sm font-semibold">{title}</div>
        </div>

        <div className="space-y-3">
          {def.fields.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-1 block text-xs text-zinc-500">
                {field.label}
                {field.required && <span className="text-danger"> *</span>}
              </span>
              {field.textarea ? (
                <textarea
                  className={textareaClass}
                  rows={3}
                  value={values[field.key]}
                  placeholder={field.placeholder}
                  onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
                />
              ) : (
                <input
                  className={inputClass}
                  value={values[field.key]}
                  placeholder={field.placeholder}
                  onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
                />
              )}
            </label>
          ))}
          {kind === 'tables' && (
            <div className="flex items-center justify-between pt-1">
              <span className="text-xs text-zinc-500">启用该表（停用后不下发给问数流程）</span>
              <Toggle checked={enabled} onChange={setEnabled} label={enabled ? '停用' : '启用'} />
            </div>
          )}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field disabled:text-zinc-400"
            disabled={pending}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="flex h-9 items-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white disabled:bg-zinc-300"
            disabled={pending || !valid}
            onClick={submit}
          >
            {pending && <LoaderCircle size={15} className="animate-spin" />}
            确定
          </button>
        </div>
      </div>
    </div>
  )
}
