import { TableProperties } from 'lucide-react'
import { PageHeader } from '../ui'

/** 元数据配置页（图4）占位 —— 完整实现在任务 #21 落地。 */
export function DatasetMetaView({ datasetId }: { datasetId: string }) {
  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={TableProperties} title="元数据配置" subtitle={`数据集 ${datasetId}`} />
      <div className="flex flex-1 items-center justify-center text-sm text-zinc-500">
        元数据配置（表结构 / 术语 / 指标 / 维度 / 外键关系 / 范例）即将上线
      </div>
    </div>
  )
}
