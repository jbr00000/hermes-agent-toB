import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, Plus } from 'lucide-react'
import { mockApi } from '../../mockApi'
import type { Dataset, DataSource, DataSourceInput } from '../../types'
import { PageHeader, cn } from '../ui'
import { ConfirmDialog } from '../ConfirmDialog'
import { DataSourceCard } from './DataSourceCard'
import { DataSourceEditModal } from './DataSourceEditModal'
import { DatasetListView } from './DatasetListView'

/** 数据库管理 tab（决策⑦）：内部含「数据源连接」（图1+图2）与「数据集」（图3）两个子视图；
 *  元数据配置（图4）是独立 detail 页，经 onOpenMeta 开新 tab。
 *  后端与算法端未接入，当前走 mockApi 内存数据——适配时平移到 api.ts。 */
export function DatabaseManageView({
  onOpenMeta,
}: {
  onOpenMeta: (dataset: Dataset) => void
}) {
  const queryClient = useQueryClient()
  const listQuery = useQuery({ queryKey: ['dataSources'], queryFn: mockApi.listDataSources })

  const [section, setSection] = React.useState<'sources' | 'datasets'>('sources')
  const [editTarget, setEditTarget] = React.useState<DataSource | null | 'create'>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<DataSource | null>(null)
  const [formError, setFormError] = React.useState<string | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['dataSources'] })

  const saveMutation = useMutation({
    mutationFn: (input: DataSourceInput) =>
      editTarget && editTarget !== 'create'
        ? mockApi.updateDataSource(editTarget.id, input)
        : mockApi.createDataSource(input),
    onSuccess: () => {
      setEditTarget(null)
      setFormError(null)
      invalidate()
    },
    onError: (err: Error) => setFormError(err.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => mockApi.deleteDataSource(id),
    onSuccess: () => {
      setDeleteTarget(null)
      invalidate()
    },
  })

  const testMutation = useMutation({
    mutationFn: (id: string) => mockApi.testDataSource(id),
    onSuccess: () => invalidate(),
  })

  const dataSources = listQuery.data ?? []

  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={Database} title="数据库管理" subtitle="数据领域知识配置 · 数据源连接与数据集" />

      {/* 子视图切换：数据源连接（图1）/ 数据集（图3） */}
      <div className="flex items-center gap-1 border-b border-line px-6 py-2">
        {([
          ['sources', '数据源连接'],
          ['datasets', '数据集'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            className={cn(
              'h-8 rounded-md px-3 text-sm transition',
              section === key ? 'bg-ink font-medium text-white' : 'text-zinc-600 hover:bg-field',
            )}
            onClick={() => setSection(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {section === 'datasets' ? (
        <DatasetListView dataSources={dataSources} onOpenMeta={onOpenMeta} />
      ) : (
        <div className="thin-scrollbar flex-1 overflow-y-auto p-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold">数据领域知识配置</div>
              <div className="mt-0.5 text-xs text-zinc-500">接入业务数据库，供问数（NL2SQL）与元数据配置使用</div>
            </div>
            <button
              className="flex h-9 items-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white"
              onClick={() => {
                setFormError(null)
                setEditTarget('create')
              }}
            >
              <Plus size={15} />
              新增连接
            </button>
          </div>

          {listQuery.isLoading ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2].map((item) => (
                <div key={item} className="h-44 animate-pulse rounded-md bg-field" />
              ))}
            </div>
          ) : dataSources.length === 0 ? (
            <div className="flex h-48 items-center justify-center rounded-md border border-dashed border-line text-sm text-zinc-500">
              暂无数据源连接，点击右上角「新增连接」接入第一个数据库
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {dataSources.map((ds) => (
                <DataSourceCard
                  key={ds.id}
                  dataSource={ds}
                  testing={testMutation.isPending && testMutation.variables === ds.id}
                  onTest={() => testMutation.mutate(ds.id)}
                  onEdit={() => {
                    setFormError(null)
                    setEditTarget(ds)
                  }}
                  onDelete={() => setDeleteTarget(ds)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {editTarget !== null && (
        <DataSourceEditModal
          target={editTarget === 'create' ? null : editTarget}
          pending={saveMutation.isPending}
          error={formError}
          onSubmit={(input) => saveMutation.mutate(input)}
          onClose={() => setEditTarget(null)}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title={`删除连接：${deleteTarget.name}`}
          description={`将删除数据源「${deleteTarget.name}」（${deleteTarget.host}:${deleteTarget.port}/${deleteTarget.database}）的连接配置，不会改动远端数据库本身。`}
          pending={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  )
}
