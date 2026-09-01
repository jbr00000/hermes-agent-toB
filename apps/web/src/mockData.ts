import type { ChatMessage, ConversationSummary, Dataset, DatasetMetaBundle, DataSource, KnowledgeSpace, MemoryCandidate, SessionSummary } from './types'

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

/** 数据源连接卡片墙（图1）种子数据：只对应阶段0 导入的三个 BULL 问数开发库
 *  （dev MySQL 容器 127.0.0.1:13306 的 nl2sql_fund / nl2sql_stock / nl2sql_macro） */
export const dataSources: DataSource[] = [
  { id: 'ds-01', name: '基金问数库', dbType: 'mysql', host: '127.0.0.1', port: 13306, database: 'nl2sql_fund', username: 'hermes_nl2sql_ro', status: 'connected', lastTestedAt: 1756195200, createdAt: 1756195200, updatedAt: 1756195200 },
  { id: 'ds-02', name: '股票问数库', dbType: 'mysql', host: '127.0.0.1', port: 13306, database: 'nl2sql_stock', username: 'hermes_nl2sql_ro', status: 'connected', lastTestedAt: 1756195200, createdAt: 1756195200, updatedAt: 1756195200 },
  { id: 'ds-03', name: '宏观问数库', dbType: 'mysql', host: '127.0.0.1', port: 13306, database: 'nl2sql_macro', username: 'hermes_nl2sql_ro', status: 'connected', lastTestedAt: 1756195200, createdAt: 1756195200, updatedAt: 1756195200 },
]

/** 数据集列表（图3）种子数据：一库一数据集。ddlCount 为真实表数（fund 28 / stock 31 / macro 19）；
 *  宏观库刻意留空提示词，演示「缺提示词」治理状态 */
export const datasets: Dataset[] = [
  { id: 'dataset-01', name: '基金问数数据集', description: 'CCKS2022 基金领域问数（ccks_fund）：公募基金概况、资产配置、费率、收益排名与风险等级查询。', dataSourceId: 'ds-01', flowVersion: '框架默认流程', enabled: true, prompt: '你是基金领域问数助手。表名以 mf_ 开头，基金内部代码统一用 InnerCode 关联，金额单位为元，日期格式 YYYY-MM-DD。', ddlCount: 28, ruleCount: 6, createdAt: 1756195200, updatedAt: 1756281600 },
  { id: 'dataset-02', name: '股票问数数据集', description: 'CCKS2022 股票领域问数（ccks_stock）：上市公司概况、分红回购、配股增发、股东与股本数据。', dataSourceId: 'ds-02', flowVersion: '框架默认流程', enabled: true, prompt: '你是股票领域问数助手。表名以 lc_ 开头，公司代码统一用 CompanyCode 关联；涉及"最新"时按信息披露日期倒序取第一条。', ddlCount: 31, ruleCount: 4, createdAt: 1756195200, updatedAt: 1756281600 },
  { id: 'dataset-03', name: '宏观问数数据集', description: 'CCKS2022 宏观经济问数（ccks_macro）：GDP、CPI、货币银行、财政收支、海关进出口等宏观指标查询。', dataSourceId: 'ds-03', flowVersion: '框架默认流程', enabled: true, prompt: '', ddlCount: 19, ruleCount: 2, createdAt: 1756195200, updatedAt: 1756281600 },
]

/** 元数据配置（图4）种子数据：表名/中文说明/外键关系取自 BULL-cn/db_info.json，
 *  范例取自 BULL-cn/dev.json 的金标准问答对 */
export const datasetMeta: Record<string, DatasetMetaBundle> = {
  'dataset-01': {
    tables: [
      { id: 'tm-01', datasetId: 'dataset-01', tableName: 'mf_fundarchives', comment: '公募基金概况', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-02', datasetId: 'dataset-01', tableName: 'mf_assetallocation', comment: '公募基金资产配置', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-03', datasetId: 'dataset-01', tableName: 'mf_chargeratenew', comment: '公募基金费率(新)', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-04', datasetId: 'dataset-01', tableName: 'mf_fundreturnrank', comment: '公募基金最新收益率排名', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-05', datasetId: 'dataset-01', tableName: 'mf_fundrisklevel', comment: '公募基金风险等级表', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-06', datasetId: 'dataset-01', tableName: 'mf_benchmarkgrowthrate', comment: '公募基金最新基准收益率', enabled: false, updatedAt: 1756281600 },
    ],
    terms: [
      { id: 'term-01', datasetId: 'dataset-01', term: '基金收益为正', definition: 'mf_fundreturnrank.fundreturn > 0', updatedAt: 1756281600 },
      { id: 'term-02', datasetId: 'dataset-01', term: '指标周期', definition: 'mf_fundreturnrank.indexcycle，取值如「一个月」「三个月」「今年以来」', updatedAt: 1756281600 },
    ],
    metrics: [
      { id: 'metric-01', datasetId: 'dataset-01', name: '正收益基金数', expression: 'COUNT(*) WHERE fundreturn > 0', description: '按指标周期与基金类别分组统计', updatedAt: 1756281600 },
    ],
    dimensions: [
      { id: 'dim-01', datasetId: 'dataset-01', name: '基金类别', field: 'mf_fundreturnrank.fundtypename', description: '股票型/混合型/债券型等', updatedAt: 1756281600 },
    ],
    foreignKeys: [
      { id: 'fk-01', datasetId: 'dataset-01', fromTable: 'mf_fundarchives', fromColumn: 'InnerCode', toTable: 'mf_assetallocation', toColumn: 'InnerCode', updatedAt: 1756281600 },
      { id: 'fk-02', datasetId: 'dataset-01', fromTable: 'mf_fundarchives', fromColumn: 'InnerCode', toTable: 'mf_fundrisklevel', toColumn: 'InnerCode', updatedAt: 1756281600 },
      { id: 'fk-03', datasetId: 'dataset-01', fromTable: 'mf_fundarchives', fromColumn: 'InnerCode', toTable: 'mf_fundreturnrank', toColumn: 'InnerCode', updatedAt: 1756281600 },
    ],
    examples: [
      { id: 'ex-01', datasetId: 'dataset-01', question: '显示指标周期为"一个月"的基金收益为正的基金数目，按基金类别分组展示', sql: "SELECT fundtypename, COUNT(*) FROM mf_fundreturnrank WHERE indexcycle = '一个月' AND fundreturn > 0 GROUP BY fundtypename", updatedAt: 1756281600 },
    ],
  },
  'dataset-02': {
    tables: [
      { id: 'tm-11', datasetId: 'dataset-02', tableName: 'lc_stockarchives', comment: '公司概况', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-12', datasetId: 'dataset-02', tableName: 'lc_actualcontroller', comment: '公司实际控制人', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-13', datasetId: 'dataset-02', tableName: 'lc_dividend', comment: '公司分红', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-14', datasetId: 'dataset-02', tableName: 'lc_buyback', comment: '股份回购', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-15', datasetId: 'dataset-02', tableName: 'lc_largeshsubscription', comment: '配股大股东认配状况', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-16', datasetId: 'dataset-02', tableName: 'lc_freefloat', comment: '自由流通股本', enabled: true, updatedAt: 1756281600 },
    ],
    terms: [
      { id: 'term-11', datasetId: 'dataset-02', term: '实配股数', definition: 'lc_largeshsubscription.actualshares；对应应配股数 oughtshares', updatedAt: 1756281600 },
    ],
    metrics: [],
    dimensions: [],
    foreignKeys: [
      { id: 'fk-11', datasetId: 'dataset-02', fromTable: 'lc_stockarchives', fromColumn: 'CompanyCode', toTable: 'lc_actualcontroller', toColumn: 'CompanyCode', updatedAt: 1756281600 },
      { id: 'fk-12', datasetId: 'dataset-02', fromTable: 'lc_stockarchives', fromColumn: 'CompanyCode', toTable: 'lc_buyback', toColumn: 'CompanyCode', updatedAt: 1756281600 },
    ],
    examples: [
      { id: 'ex-11', datasetId: 'dataset-02', question: '哪些股东名称的实配和应配股数都超过50万', sql: 'SELECT shname FROM lc_largeshsubscription WHERE actualshares > 500000 AND oughtshares > 500000', updatedAt: 1756281600 },
    ],
  },
  'dataset-03': {
    tables: [
      { id: 'tm-21', datasetId: 'dataset-03', tableName: 'ed_grossdomesticproduct', comment: '国内生产总值', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-22', datasetId: 'dataset-03', tableName: 'ed_consumerpriceindex', comment: '居民消费价格指数', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-23', datasetId: 'dataset-03', tableName: 'ed_chinamoneyandbanking', comment: '中国货币与银行概览', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-24', datasetId: 'dataset-03', tableName: 'ed_exportimport', comment: '海关进出口', enabled: true, updatedAt: 1756281600 },
      { id: 'tm-25', datasetId: 'dataset-03', tableName: 'ed_otherdepositorycorpbs', comment: '其他存款性公司资产负债表', enabled: true, updatedAt: 1756281600 },
    ],
    terms: [],
    metrics: [],
    dimensions: [],
    foreignKeys: [],
    examples: [
      { id: 'ex-21', datasetId: 'dataset-03', question: '列出准备金存款在其他存款性公司资产负债表中的数据记录', sql: 'SELECT depositswithcentralbank FROM ed_otherdepositorycorpbs', updatedAt: 1756281600 },
    ],
  },
}
