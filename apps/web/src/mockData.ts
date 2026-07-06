import type { ChatMessage, KnowledgeDocument, KnowledgeSpace, MemoryCandidate, SessionSummary, UserRow } from './types'

export const spaces: KnowledgeSpace[] = [
  { id: 'rail', name: '轨道公司', role: 'space_admin', libraries: 4, documents: 128 },
  { id: 'cost', name: '费用测算', role: 'contributor', libraries: 3, documents: 76 },
  { id: 'contract', name: '合同资料', role: 'member', libraries: 2, documents: 43 },
]

export const sessions: SessionSummary[] = [
  { id: 's-1001', title: '智库平台费用测算文档总结', space: '轨道公司', status: 'running', updatedAt: '09:42', risk: 'medium' },
  { id: 's-1002', title: '合同条款差异分析', space: '合同资料', status: 'plan_pending', updatedAt: '昨天', risk: 'high' },
  { id: 's-1003', title: '知识库入库规则梳理', space: '费用测算', status: 'completed', updatedAt: '周五', risk: 'low' },
]

export const messages: Record<string, ChatMessage[]> = {
  's-1001': [
    { id: 'm-1', role: 'user', content: '请读取费用测算表，提炼主要测算口径并输出本地 txt。', createdAt: '09:38' },
    { id: 'm-2', role: 'assistant', content: '已读取 4 个工作表。文档主要覆盖软件开发费用构成、人员投入测算、交付阶段拆分和风险预备费口径。', createdAt: '09:39' },
    { id: 'm-3', role: 'system', content: '检测到结果文件写入动作，当前需要受控写入权限。', createdAt: '09:40' },
  ],
  's-1002': [
    { id: 'm-4', role: 'user', content: '对比 2024 版与 2026 版合同模板，标记付款和验收风险。', createdAt: '昨天' },
    { id: 'm-5', role: 'assistant', content: '该任务包含共享知识库更新建议，需要先生成计划并获得批准。', createdAt: '昨天' },
  ],
}

export const documents: KnowledgeDocument[] = [
  { id: 'd-001', title: '智库平台-软件开发费用测算V0.2.xlsx', spaceId: 'rail', library: '项目测算库', status: 'ready', permission: 'override', owner: 'alice', updatedAt: '今天 09:21', chunks: 64 },
  { id: 'd-002', title: '轨道公司智能管控平台建设方案.docx', spaceId: 'rail', library: '项目资料库', status: 'parsing', permission: 'inherited', owner: 'bob', updatedAt: '今天 08:55', chunks: 0 },
  { id: 'd-003', title: '软件开发人员投入基准.xlsx', spaceId: 'cost', library: '费用测算库', status: 'ready', permission: 'inherited', owner: 'carol', updatedAt: '昨天 18:02', chunks: 38 },
  { id: 'd-004', title: '合同验收条款风险清单.pdf', spaceId: 'contract', library: '合同风险库', status: 'review', permission: 'override', owner: 'admin', updatedAt: '周五 16:12', chunks: 41 },
]

export const memoryCandidates: MemoryCandidate[] = [
  { id: 'c-01', content: '费用测算任务默认优先输出“测算口径、费用构成、风险项、待确认问题”四段。', source: '智库平台费用测算文档总结', status: 'pending' },
  { id: 'c-02', content: '轨道公司相关资料默认使用“轨道公司”业务空间知识库。', source: '历史会话', status: 'pending' },
]

export const users: UserRow[] = [
  { id: 'u-1', username: 'admin', role: 'admin', spaces: ['轨道公司', '费用测算', '合同资料'] },
  { id: 'u-2', username: 'alice', role: 'user', spaces: ['轨道公司', '费用测算'] },
  { id: 'u-3', username: 'bob', role: 'user', spaces: ['轨道公司'] },
]
