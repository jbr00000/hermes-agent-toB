import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Brain, Check, Plus } from 'lucide-react'
import { mockApi } from '../mockApi'
import type { MemoryCandidate } from '../types'
import { PageHeader } from '../components/ui'

/** 记忆中心 —— 当前为 mock 数据源（个人记忆后端未接入），仅作展示原型。 */
export function MemoryView() {
  const query = useQuery({ queryKey: ['memoryCandidates'], queryFn: mockApi.listMemoryCandidates })
  const [items, setItems] = React.useState<MemoryCandidate[]>([])

  React.useEffect(() => {
    setItems(query.data ?? [])
  }, [query.data])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={Brain} title="记忆中心" subtitle="个人记忆与待审核候选" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">待审核候选</div>
            <div className="text-xs text-zinc-500">批准后写入个人长期记忆</div>
          </div>
          <button className="flex h-8 items-center gap-2 rounded-md border border-line px-3 text-sm hover:bg-field">
            <Plus size={15} />
            新增记忆
          </button>
        </div>
        <div className="divide-y divide-line border-y border-line">
          {items.map((item) => (
            <div key={item.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-5 py-4">
              <div>
                <div className="text-sm text-zinc-800">{item.content}</div>
                <div className="mt-2 text-xs text-zinc-500">来源：{item.source}</div>
              </div>
              <div className="flex items-center gap-2">
                <button className="h-8 rounded-md border border-line px-3 text-sm hover:bg-field" onClick={() => setItems((current) => current.filter((candidate) => candidate.id !== item.id))}>忽略</button>
                <button className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-3 text-sm text-white" onClick={() => setItems((current) => current.filter((candidate) => candidate.id !== item.id))}>
                  <Check size={14} />
                  批准
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
