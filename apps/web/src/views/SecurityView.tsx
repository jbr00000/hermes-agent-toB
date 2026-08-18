import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, FileCheck2, KeyRound, LockKeyhole, ShieldCheck, type LucideIcon } from 'lucide-react'
import { api } from '../api'
import { InfoRow, PageHeader } from '../components/ui'

export function SecurityView() {
  const query = useQuery({ queryKey: ['features'], queryFn: api.getFeatures })
  const features = query.data
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={ShieldCheck} title="能力与安全" subtitle="运行模式、工具权限与高风险门控" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        <div className="grid gap-5 xl:grid-cols-2">
          <PlainPanel title="运行环境">
            {/* 后端 /features 不提供 provider/model，这里只展示真实返回的安全姿态 */}
            <InfoRow
              label="Sandbox"
              value={features?.host_terminal ? '宿主机（host_terminal 已开启）' : 'Docker 容器（默认沙箱）'}
            />
            <InfoRow label="Host terminal" value={features?.host_terminal ? '开启' : '关闭'} />
          </PlainPanel>
          <PlainPanel title="权限模式策略">
            <PolicyRow icon={LockKeyhole} title="只读模式" text="问答、检索、读取授权文件、生成计划" />
            <PolicyRow icon={FileCheck2} title="受控写入" text="终端命令需逐条经你批准后执行" />
            <PolicyRow icon={KeyRound} title="完全访问" text="共享知识库修改、数据库写入、终端命令" />
          </PlainPanel>
          {features?.dataPermissions.enabled && (
            <PlainPanel title="数据权限">
              <InfoRow
                label="可访问的表"
                value={
                  features.dataPermissions.allowedTables && features.dataPermissions.allowedTables.length > 0
                    ? features.dataPermissions.allowedTables.join('、')
                    : '无（全部禁止）'
                }
              />
              <div className="mt-2 text-xs text-zinc-500">
                当前角色只能查询以上业务表；越权 SQL 会被拦截并记入审计。
              </div>
            </PlainPanel>
          )}
        </div>
        <div className="mt-6 border-y border-line py-4">
          <div className="mb-3 text-sm font-semibold">高风险操作</div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {['修改共享知识库', '数据库写操作', '终端命令', '批量删除'].map((item) => (
              <div key={item} className="flex items-center gap-2 rounded-md bg-field px-3 py-2 text-sm">
                <AlertTriangle size={15} className="text-caution" />
                {item}
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  )
}

function PlainPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-y border-line py-4">
      <div className="mb-3 text-sm font-semibold">{title}</div>
      <div className="space-y-3">{children}</div>
    </section>
  )
}

function PolicyRow({ icon: Icon, title, text }: { icon: LucideIcon; title: string; text: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-field">
        <Icon size={15} />
      </div>
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="mt-0.5 text-xs text-zinc-500">{text}</div>
      </div>
    </div>
  )
}
