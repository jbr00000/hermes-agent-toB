import type { ChatMessage, ConversationSummary, Dataset, DataSource, KnowledgeSpace, MemoryCandidate, SessionSummary } from './types'

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

/** 数据源连接卡片墙（图1）种子数据；status 模拟最近一次测试连接的结果 */
export const dataSources: DataSource[] = [
  { id: 'ds-01', name: '地下水治理', dbType: 'postgresql', host: '192.168.1.137', port: 5432, database: 'appdb', username: 'appuser', status: 'connected', lastTestedAt: 1756108800, createdAt: 1755936000, updatedAt: 1756108800 },
  { id: 'ds-02', name: '工作督办', dbType: 'mysql', host: '192.168.1.86', port: 3307, database: 'business_data', username: 'reader', status: 'connected', lastTestedAt: 1756022400, createdAt: 1755849600, updatedAt: 1756022400 },
  { id: 'ds-03', name: '计划调度', dbType: 'mysql', host: '192.168.1.90', port: 3306, database: 'schedule_plan', username: 'scheduler', status: 'connected', lastTestedAt: 1756022400, createdAt: 1755763200, updatedAt: 1756022400 },
  { id: 'ds-04', name: '资产管理', dbType: 'mysql', host: '192.168.1.101', port: 3306, database: 'asset_management', username: 'asset_ro', status: 'failed', lastTestedAt: 1755936000, createdAt: 1755676800, updatedAt: 1755936000 },
  { id: 'ds-05', name: '渗漏治理', dbType: 'postgresql', host: '192.168.1.86', port: 5432, database: 'dpc', username: 'dpc_ro', status: 'untested', lastTestedAt: null, createdAt: 1756108800, updatedAt: 1756108800 },
  // BULL 问数开发库（阶段0 已导入 dev MySQL 容器 127.0.0.1:13306 的三个 schema）
  { id: 'ds-06', name: '基金问数库', dbType: 'mysql', host: '127.0.0.1', port: 13306, database: 'nl2sql_fund', username: 'hermes_nl2sql_ro', status: 'connected', lastTestedAt: 1756195200, createdAt: 1756195200, updatedAt: 1756195200 },
  { id: 'ds-07', name: '股票问数库', dbType: 'mysql', host: '127.0.0.1', port: 13306, database: 'nl2sql_stock', username: 'hermes_nl2sql_ro', status: 'connected', lastTestedAt: 1756195200, createdAt: 1756195200, updatedAt: 1756195200 },
  { id: 'ds-08', name: '宏观问数库', dbType: 'mysql', host: '127.0.0.1', port: 13306, database: 'nl2sql_macro', username: 'hermes_nl2sql_ro', status: 'connected', lastTestedAt: 1756195200, createdAt: 1756195200, updatedAt: 1756195200 },
]

/** 数据集列表（图3）种子数据：覆盖 启用 / 缺提示词 / 缺业务说明 / 停用 四种治理状态 */
export const datasets: Dataset[] = [
  { id: 'dataset-01', name: '基金问数数据集', description: 'CCKS2022 基金领域问数：基金基本信息、规模、费率、持仓与净值查询。', dataSourceId: 'ds-06', flowVersion: '框架默认流程', enabled: true, prompt: '你是基金领域问数助手。表名与字段均为中文拼音，金额单位为元，日期格式 YYYY-MM-DD。', ddlCount: 28, ruleCount: 6, createdAt: 1756195200, updatedAt: 1756281600 },
  { id: 'dataset-02', name: '股票问数数据集', description: 'CCKS2022 股票领域问数：上市公司基本信息、行情、财务指标与股东数据。', dataSourceId: 'ds-07', flowVersion: '框架默认流程', enabled: true, prompt: '你是股票领域问数助手。股票代码为 6 位数字字符串，涉及"最新"时按交易日期倒序取第一条。', ddlCount: 15, ruleCount: 4, createdAt: 1756195200, updatedAt: 1756281600 },
  { id: 'dataset-03', name: '宏观问数数据集', description: 'CCKS2022 宏观经济问数：GDP、CPI、利率、汇率等宏观指标查询。', dataSourceId: 'ds-08', flowVersion: '框架默认流程', enabled: true, prompt: '', ddlCount: 10, ruleCount: 2, createdAt: 1756195200, updatedAt: 1756281600 },
  { id: 'dataset-04', name: '督办事项统计', description: '', dataSourceId: 'ds-02', flowVersion: '框架默认流程', enabled: true, prompt: '统计口径以督办单状态字段为准，逾期 = 截止时间早于当前且状态非已完成。', ddlCount: 8, ruleCount: 3, createdAt: 1756108800, updatedAt: 1756195200 },
  { id: 'dataset-05', name: '资产台账问数', description: '资产台账与折旧查询（待资产库连接恢复后启用）。', dataSourceId: 'ds-04', flowVersion: '框架默认流程', enabled: false, prompt: '金额单位为万元，保留两位小数。', ddlCount: 0, ruleCount: 0, createdAt: 1756022400, updatedAt: 1756108800 },
]
