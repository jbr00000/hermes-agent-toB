import type { ChatMessage, ConversationSummary, KnowledgeSpace, MemoryCandidate, SessionSummary } from './types'

export const spaces: KnowledgeSpace[] = [
  { id: 'rail', name: '轨道公司', role: 'space_admin', libraries: 4, documents: 128 },
  { id: 'cost', name: '费用测算', role: 'contributor', libraries: 3, documents: 76 },
  { id: 'contract', name: '合同资料', role: 'member', libraries: 2, documents: 43 },
]

export const sessions: SessionSummary[] = [
  { id: 's-1001', title: '智库平台费用测算文档总结', space: '轨道公司', status: 'running', updatedAt: '09:42', risk: 'medium' },
  { id: 's-1002', title: '合同条款差异分析', space: '合同资料', status: 'awaiting_approval', updatedAt: '昨天', risk: 'high' },
  { id: 's-1003', title: '知识库入库规则梳理', space: '费用测算', status: 'completed', updatedAt: '周五', risk: 'low' },
]

export const conversations: ConversationSummary[] = [
  { id: 'c-2001', title: '费用测算口径咨询', space: '轨道公司', updatedAt: '10:16', period: 'today', pinned: true },
  { id: 'c-2002', title: '轨道检修计划约束', space: '轨道公司', updatedAt: '09:28', period: 'today', pinned: true },
  { id: 'c-2003', title: '合同付款节点解释', space: '合同资料', updatedAt: '昨天', period: 'yesterday' },
  { id: 'c-2004', title: '知识库文档引用范围', space: '费用测算', updatedAt: '昨天', period: 'yesterday' },
  { id: 'c-2005', title: '项目验收材料清单', space: '轨道公司', updatedAt: '7 月 3 日', period: 'earlier' },
  { id: 'c-2006', title: '软件开发成本构成', space: '费用测算', updatedAt: '6 月 28 日', period: 'earlier' },
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
  'c-2001': [
    { id: 'cm-1', role: 'user', content: '这份费用测算表采用了什么测算口径？', createdAt: '10:14' },
    { id: 'cm-2', role: 'assistant', content: '当前文档采用功能点规模估算口径，并结合调整因子折算软件规模。现有条目中仍有较多功能项未完成计数，因此适合用于说明测算方法，但暂时不能作为最终费用结论。', createdAt: '10:15' },
  ],
  'c-2002': [
    { id: 'cm-3', role: 'user', content: '轨道检修计划通常需要考虑哪些约束？', createdAt: '09:26' },
    { id: 'cm-4', role: 'assistant', content: '主要约束包括天窗时间、线路占用、人员资质、工器具状态、作业冲突和应急预案。回答引用了“轨道公司”业务空间内已授权的制度文档。', createdAt: '09:27' },
  ],
}

export const memoryCandidates: MemoryCandidate[] = [
  { id: 'c-01', content: '费用测算任务默认优先输出“测算口径、费用构成、风险项、待确认问题”四段。', source: '智库平台费用测算文档总结', status: 'pending' },
  { id: 'c-02', content: '轨道公司相关资料默认使用“轨道公司”业务空间知识库。', source: '历史会话', status: 'pending' },
]
