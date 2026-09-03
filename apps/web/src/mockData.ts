import type { ChatMessage, ConversationSummary, KnowledgeSpace, MemoryCandidate, SessionSummary } from './types'

export const spaces: KnowledgeSpace[] = [
  { id: 'litigation', name: '诉讼仲裁', role: 'space_admin', libraries: 5, documents: 132 },
  { id: 'advisory', name: '常年法律顾问', role: 'contributor', libraries: 3, documents: 87 },
  { id: 'contract', name: '合同审查', role: 'member', libraries: 2, documents: 64 },
]

export const sessions: SessionSummary[] = [
  { id: 's-1001', title: '建设合同纠纷证据材料梳理', space: '诉讼仲裁', status: 'running', updatedAt: '09:42', risk: 'medium' },
  { id: 's-1002', title: '股权转让协议条款差异分析', space: '合同审查', status: 'awaiting_approval', updatedAt: '昨天', risk: 'high' },
  { id: 's-1003', title: '顾问单位制度合规要点梳理', space: '常年法律顾问', status: 'completed', updatedAt: '周五', risk: 'low' },
]

export const conversations: ConversationSummary[] = [
  { id: 'c-2001', title: '劳动仲裁时效与举证责任咨询', space: '诉讼仲裁', updatedAt: '10:16', period: 'today', pinned: true },
  { id: 'c-2002', title: '建设工程价款优先受偿权要点', space: '诉讼仲裁', updatedAt: '09:28', period: 'today', pinned: true },
  { id: 'c-2003', title: '顾问合同付款节点解释', space: '常年法律顾问', updatedAt: '昨天', period: 'yesterday' },
  { id: 'c-2004', title: '合同审查意见引用范围', space: '合同审查', updatedAt: '昨天', period: 'yesterday' },
  { id: 'c-2005', title: '尽职调查材料清单核对', space: '诉讼仲裁', updatedAt: '7 月 3 日', period: 'earlier' },
  { id: 'c-2006', title: '公司章程修订要点', space: '常年法律顾问', updatedAt: '6 月 28 日', period: 'earlier' },
]

export const messages: Record<string, ChatMessage[]> = {
  's-1001': [
    { id: 'm-1', role: 'user', content: '请梳理本案证据清单，提炼争议焦点并输出本地 txt。', createdAt: '09:38' },
    { id: 'm-2', role: 'assistant', content: '已读取 4 份材料。本案争议焦点集中在工程款结算依据、逾期付款利息起算点和质保金返还条件。', createdAt: '09:39' },
    { id: 'm-3', role: 'system', content: '检测到结果文件写入动作，当前需要受控写入权限。', createdAt: '09:40' },
  ],
  's-1002': [
    { id: 'm-4', role: 'user', content: '对比 2024 版与 2026 版股权转让协议模板，标记付款与陈述保证条款风险。', createdAt: '昨天' },
    { id: 'm-5', role: 'assistant', content: '该任务包含共享知识库更新建议，需要先生成计划并获得批准。', createdAt: '昨天' },
  ],
  'c-2001': [
    { id: 'cm-1', role: 'user', content: '这个案子的劳动仲裁时效怎么算？', createdAt: '10:14' },
    { id: 'cm-2', role: 'assistant', content: '劳动争议申请仲裁的时效期间为一年，自当事人知道或应当知道其权利被侵害之日起计算。劳动关系存续期间因拖欠劳动报酬发生争议的，不受一年时效限制，但劳动关系终止的，应当自终止之日起一年内提出。', createdAt: '10:15' },
  ],
  'c-2002': [
    { id: 'cm-3', role: 'user', content: '主张建设工程价款优先受偿权要注意哪些要点？', createdAt: '09:26' },
    { id: 'cm-4', role: 'assistant', content: '主要要点包括行使期限（最长十八个月）、优先受偿的范围（不含违约金与利息）、与抵押权和消费者购房人权利的顺位关系。回答引用了“诉讼仲裁”业务空间内已授权的法规与判例文档。', createdAt: '09:27' },
  ],
}

export const memoryCandidates: MemoryCandidate[] = [
  { id: 'c-01', content: '合同纠纷类任务默认优先输出“争议焦点、证据清单、法律依据、待核实问题”四段。', source: '建设合同纠纷证据材料梳理', status: 'pending' },
  { id: 'c-02', content: '诉讼案件资料默认使用“诉讼仲裁”业务空间知识库。', source: '历史会话', status: 'pending' },
]
