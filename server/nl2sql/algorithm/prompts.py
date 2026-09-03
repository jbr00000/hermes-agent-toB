"""问数提示词 —— 移植 lone-ai ``core/prompt_bank/prompt_nl2sql.py`` 的净化版。

剔除项（相对 lone-ai）：
  - 澄清/追问/改写/问题分解四组提示词整体删除（产品决策：不做澄清与分解）
  - 实体抽取里的水项目特例（"1-八通线属于非时间实体"、"六号线东大桥站"例）
  - SQL 生成的复合问题规则 15/16（整段是 dws_sbm_*/监测井水位的业务口径）
  - 通用规则里的业务硬编码：故障数量默认排序、"优先使用 dws_line_name/
    dws_station_name"、"水位数据优先限制 jlsj" 等；保留通用方法论
  - 结果后处理/可移除数量字段提示词里的监测井、水位示例 → 泛化表述
"""
from __future__ import annotations

import json
import time
from typing import Any


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class PromptBuilder:
    """问数各阶段的 LLM 提示词构建。"""

    # ==================== 实体抽取 ====================

    @staticmethod
    def build_entity_extraction_prompt(question: str) -> str:
        """构建实体抽取提示词"""
        return f"""# 任务
            从用户问题中提取所有关键实体。

            # 用户问题
            {question}

            # 实体分类
            1. **时间型实体**：任何与时间相关的表达
            - 绝对时间：2024-01-01、2024年1月
            - 相对时间：最近3天、本周、上月
            - 模糊时间：今年、本季度

            2. **指标型实体**：任何与指标相关的表述
            包括但不限于：销售额，故障数等

            3. **其他实体**：除时间实体和指标型实体外的其他实体，
            - 包括但不限于：公司名、专业名、产品名、设备名、类型名、地点名等
            - 须留意实体位置连续的情况，需拆解为多个实体（如"甲公司乙部门"需拆解为"甲公司"和"乙部门"）

            # 输出要求
            请以JSON格式输出，并将结果放置于<result></result>标签内。
            {{
                "time_entities": ["实体1", "实体2", ...],
                "metric_entities": ["实体1", "实体2", ...],
                "other_entities": ["实体1", "实体2", ...]
            }}
            /no_think
            """

    # ==================== 实体候选值排序 ====================

    @staticmethod
    def build_entity_ranking_prompt(question: str, entity: str, candidates_dict: dict) -> str:
        """构建实体候选值排序提示词"""
        return f"""# 任务
            1、根据用户问题和实体候选值，选择最合适的5个候选值并排序
            2、生成从1中选择的5个候选值中挑选唯一候选值的困惑度分数

            # 排序原则
            1、与待匹配实体完全一致的候选实体优先
            2、存在多个与候选实体一致的候选实体时，候选实体所在表的表名与问题一致性较高的优先

            # 用户问题
            {question}

            # 待匹配实体
            {entity}

            # 候选值列表
            {candidates_dict}

            # 选择标准
            1. **语义匹配度**：候选值与实体的语义相似程度
            2. **上下文相关性**：候选值在当前问题上下文中的合理性
            3. **业务逻辑**：候选值是否符合业务场景

            # 输出要求
            请以JSON格式输出，仅需以列表输出"候选值"+序号，返回Top-5排序结果，并将结果放置于<result></result>标签内。
            <result>
            {{
                "top_candidates": ["候选值1", "候选值2", "候选值3", "候选值4", "候选值5"],
                "ambiguity_level": "模糊程度，范围在0-1之间，1代表模糊性最高，0代表模糊性最低"
            }}
            </result>

            # 排序原则示例
            依据排序原则，候选值X和候选值Y和待匹配实体完全一致，其余候选值依据语义相似性获得。
            候选值Y所在表的表名更贴近用户问题中的查询需求，因此候选值Y排在候选值X前面
            /no_think
            """

    # ==================== Schema 预挑选 ====================

    @staticmethod
    def build_schema_selection_prompt(
        question: str, ddl_candidates: list[dict], retrieval_info: dict
    ) -> str:
        """构建 Schema 预挑选提示词"""
        ddl_text = ""
        for table_info in ddl_candidates:
            fields_text = "\n".join(table_info.get("fields", []))
            ddl_text += f"""
                表名: {table_info['table_name']}
                字段: {fields_text}
                """

        return f"""# 任务
            从候选DDL中选择适合回答用户问题的表结构。

            # 用户问题
            {question}

            # 候选DDL
            {ddl_text}

            # 表格选择逻辑
            1. **表数量最小化**（P1）：相关表必须能够回答用户问题，在此基础上，表格数量越少越好。

            # 输出要求
            请以JSON格式输出，并将结果放置于<result></result>标签内。
            <result>["表名1", "表名2"]</result>
            /no_think
            """

    # ==================== SQL 生成 ====================

    @staticmethod
    def build_sql_generation_prompt(
        question: str,
        table_ddl: list[str],
        join_path: str,
        steps: list | None,
        few_shots_information: list[dict],
        terminology_infomation: list[dict],
        index_information: list[dict],
        dimension_information: dict,
        db_type: str,
    ) -> str:
        """构建 SQL 生成提示词"""
        schema_text = ""
        decompose_text = ""
        join_text = ""
        few_shots_text = ""
        index_text = ""
        terminology_text = ""
        dimension_text = ""

        if table_ddl:
            schema_text = "##生成SQL可能使用到的相关表格ddl信息\n"
            schema_text += "\n".join(table_ddl)

        if steps:
            decompose_text = "### 用户查询意图可分解为以下几个步骤：\n"
            decompose_text += "\n".join([f"{str(ele)}" for ele in steps])

        if join_path:
            join_text = "##生成SQL可能使用的多表关联信息\n"
            join_text += join_path

        if few_shots_information:
            few_shots_text = "### 与用户查询近似的历史查询问题与SQL语言如下：\n"
            few_shots_text += "\n".join([
                f"示例问题{i+1}：{ele['question']}，SQL查询语言：{ele['question_sql']}\n"
                for i, ele in enumerate(few_shots_information[:5])
            ])

        if index_information:
            index_text = "### 用户查询可能使用到的计算指标计算信息如下：\n"
            for i, chunk in enumerate(index_information):
                table_columns = chunk["index_name"].split("&")
                index_text += (
                    f"-指标名称{i+1}：{chunk['index_display_name']}，"
                    f"指标计算方式：{chunk['calculate_method']}，指标计算涉及到以下表和其中的列\n"
                )
                for ele in table_columns:
                    if "." not in ele:
                        continue
                    table_name, column_name = ele.rsplit(".", 1)
                    index_text += f"- 表名称：{table_name}， 列名称：{column_name}\n"

        if terminology_infomation:
            terminology_text = "#### 用户查询相关的术语解释或规则：\n"
            for chunk in terminology_infomation:
                terminology_text += f"            -术语：{chunk['terminology']}, 该术语相关解释如下\n"
                terminology_text += f"            {chunk['terminology_explain']}\n"
                if chunk.get("synonyms", ""):
                    terminology_text += f"            {chunk['terminology']}等同于：{chunk['synonyms']}\n"

        if dimension_information:
            dimension_text = "### 用户查询可能使用的的数据库标准值及该值所处字段如下：\n"
            for entity, chunks in dimension_information.items():
                if chunks:
                    dimension_text += f"            # 用户查询中的实体为：{entity}，可能使用的数据库标准值如下：\n"
                    standard_values_index = [
                        i for i, chunk in enumerate(chunks) if chunk.get("value", "") == entity
                    ]
                    if standard_values_index:
                        chunks = [chunks[i] for i in standard_values_index]

                    for i, chunk in enumerate(chunks):
                        table_name, column_name = chunk["table_name"], chunk["column_name"]
                        source = chunk["source"]
                        if source == "数据库中的列":
                            if column_name.lower().strip().split("_")[-1] != "id":
                                dimension_text += f"            - 可能使用{table_name}表中的{column_name}列，该列的中文含义为{chunk['value']}\n"
                        else:
                            if (
                                chunk.get("value", "")
                                and chunk.get("key", "")
                                and chunk.get("value", "") != chunk.get("key", "")
                            ):
                                dimension_text += f"            - 可能的数据库标准值{i+1}是“{chunk['value']}”，该值处于{table_name}表的{column_name}列下，该值在列下的真实值是{chunk['key']}\n"
                            else:
                                dimension_text += f"            - 可能的数据库标准值{i+1}是“{chunk['value']}”，该值处于{table_name}表的{column_name}列下\n"

        return f"""# 任务
            根据用户问题生成完整的SQL查询语句。

            ### 数据库类型
            {db_type}

            #### 通用规则
            0、当涉及非ddl中的字段名称时，将其别名As为合适的中文
            1、逐字逐句仔细查看问题和数据库模式，以便恰当地回答问题。
            2、使用最相关的数据库标准值作为检索条件
            3、尽量不使用FULL OUTER JOIN
            5、统计分布情况或占比情况时，无需将数值转化为百分比
            5.1、当用户同时询问“数量/多少/存量/总数”和“分布在哪些维度”时，必须保持原始业务对象粒度，再按分布维度 GROUP BY 统计业务对象数量。
            6、用户问题中无明显分组或排序需求时，默认按照数量指标进行降序排列
            7、尽量使用数据库中与用户查询一致的标准值
            8、按时间统计时，如按年、按月，默认按照时间增长的顺序排列
            9、将文本字段转换为数值类型时，必须先使用 NULLIF(TRIM(字段), '') 排除空串，并使用与数据库类型匹配的转换函数
            10、用户提到“近期”但未明确时间范围时，默认按近三个月处理，使用与数据库类型匹配的日期函数计算时间范围。
            11、问题中只要同时存在“全局汇总指标”和“多个分布对象/明细对象”，就按 1-N 结果拼接处理，不依赖用户问题中的表达顺序。全局汇总指标包括总数量、总数、存量、总体比例等，只能在独立 CTE 中按全局口径计算一次，例如 total_count；分布对象/明细对象应在另一个 CTE 中生成 N 行。最终 SELECT 时将全局汇总指标通过 CROSS JOIN 或等价方式扩展到每一行分布对象。
            12、用户问的是“是否、有无、有没有、是否存在、是否超限、是否完成、是否达标、是否满足、是否异常、是否需要关注”等布尔判断类问题，应优先输出面向问题的判断结果字段，例如“是否超限”“是否存在”。输出判断结果字段时，还必须同时输出支撑判断的证据字段，例如相关数量、状态、时间、监测值、阈值或差值，便于用户理解判断依据。如果问题提到“超限”，必须输出当前值、对应阈值和差值字段。
            13、当用户询问数量、总数、次数、个数、存量、累计等数量类结果时，最终 SELECT 需要同时返回总数量、该总数量的来源分布明细、以及判断这些记录为何被统计进来的证据字段。总数量应按用户问题的完整筛选条件计算一次，并使用清晰中文别名；分布明细应按问题中的分布维度联合统计数量；证据字段应覆盖用于判定记录被纳入统计的关键条件，例如状态、类型、时间、业务分类、当前值、阈值、差值或其他筛选条件字段。最终结果应能同时说明“总共有多少”“这些数量分别分布在哪些维度、各有多少”“为什么这些记录应被统计进来”。
            14、生成 SQL 的原则是完整证据链：凡是输出结论型结果，SELECT 中应同时输出结论、结论来源和判定依据。数量结论需要给出总数、分布维度的数量明细，以及支撑统计口径的关键条件字段；布尔或超限结论需要给出判断结果、当前值、对应阈值、差值字段、状态/时间/对象等证据字段，便于用户自行判断结论是否正确。

            ### 用户问题
            {question}

            ## 当前时间
            {_now_text()}

            {schema_text}

            {join_text}

            {decompose_text}

            {few_shots_text}

            {terminology_text}

            {index_text}

            {dimension_text}

            ### SQL解释模板：
            1、从表中进行数据筛选
            2、统计不同的业务主键作为数量（如果有 Count(DISTINCT ...) 语句，添加此解释）
            3、XX字段（字段名称需要为中文）的条件是XX
            4、为所有检索字段添加了非空限制
            5、按照XX字段进行了分组（如果有groupby条件，添加此解释）
            6、仅筛选了XX条数据（如果有limit条件，添加此解释）
            7、按照XX顺序进行排列（如果有order by条件，添加此解释）

            # 输出要求
            以json格式进行输出，并将结果放置于<result></result>标签内

            <result>
            {{
                "sql":完整的SQL语句,
                "explain": "SQL解释，每项换行带序号"
            }}
            </result>
            /no_think
            """

    # ==================== 结果后处理（复合问题字段拆分） ====================

    @staticmethod
    def build_result_post_process_prompt(
        question: str, sql_content: str, compact_result: dict[str, Any]
    ) -> str:
        """构建复合问题 SQL 执行结果后处理提示词"""
        return f"""请基于用户复合问题、SQL 和 SQL 执行结果字段，完成以下判断。

要求：
1、必须先判断子问题关系类型 relation_type，再拆分业务字段。
2、将复合问题按业务领域拆分为子问题。相邻问题如果属于同一个业务领域，必须合并为一个子问题。
3、业务领域需根据问题语义灵活命名，便于后续接入更多业务。
4、判断子问题之间是否存在结果依赖关系，例如后一个子问题是否依赖前一个子问题的结果集合。
5、判断 SQL 执行结果中的每个字段对应哪个子问题。
6、判断哪些字段是非依赖拆分时应带入每个业务结果的公共条件字段 common_fields。
7、只允许输出 JSON，且必须放在<result></result>标签中。

关系类型判断规则：
1、parallel：并列展示关系。用户分别询问多个业务结果，且每个业务结果可以独立回答。典型表达包括“同时看看A和B”“A是多少，B是多少”。这种情况 has_dependency=false，可以拆分展示。注意：“分别”不一定表示并列展示；如果“分别”修饰的是前一个子问题筛选出的对象集合，则属于 condition_dependency。
2、intersection：交集筛选关系。用户使用“且、并且、同时满足、既...又...、哪些对象A且B”等表达，要求返回同时满足多个业务条件的对象。这种情况 has_dependency=true。
3、condition_dependency：条件限定关系。一个子问题的结果作为另一个子问题的限定范围，例如“满足条件A的对象中，哪些还满足条件B”。这种情况 has_dependency=true。
4、如果用户先提出一个业务筛选对象集合，随后对“这些对象、它们、分别、对应、上述对象”统计另一个业务的数量、状态或明细，即使没有出现“且、并且、在...中”，也应判断为 condition_dependency，has_dependency=true。典型表达包括：“A的对象有哪些，分别有多少B”。
5、如果 SQL 执行结果只输出共同对象字段（例如各类名称字段），且问题中存在“且、并且、同时满足、既...又...”等交集表达，应判断为 intersection，has_dependency=true。

字段映射规则：
1、如果 has_dependency=false，字段尽量只归属一个子问题，便于拆分展示。
2、如果 has_dependency=true 且最终 SQL 结果是交集或筛选后的对象集合，允许同一个共同对象字段同时归属多个子问题。
3、不要因为某个业务没有专属指标字段，就返回空字段列表；如果该子问题通过共同对象字段体现筛选结果，也应映射共同对象字段。
4、事件时间字段必须优先归属产生该时间的源业务子问题；后续子问题只是用该时间做“当时、同期、发生时”的回查参照时，不要把事件时间字段只归属到后一个子问题。
5、如果无法可靠拆分字段，但能判断是依赖或交集问题，应设置 has_dependency=true，并保守映射共同对象字段。

公共条件字段判断规则：
1、common_fields 必须从 SQL 执行结果字段中选择，不能编造字段。
2、common_fields 表示用户问题中的已知限制条件或共享对象，用于让拆分后的每个业务结果保留必要上下文。
3、common_fields 不等于所有维度字段。只选择能表达用户已知条件、共享对象或共同筛选范围的字段。
4、不要仅因为字段看起来像名称、编码、类型或时间就加入 common_fields；只有它们是用户问题中的已知条件或所有子问题共同依赖的对象时才加入。
5、如果没有可靠公共条件字段，common_fields 输出空列表。

输出 JSON 格式：
<result>
{{
  "relation_type": "parallel | intersection | condition_dependency",
  "sub_questions": [
    {{
      "id": "q1",
      "business_domain": "业务领域名称",
      "question": "合并后的子问题",
      "depends_on": []
    }}
  ],
  "has_dependency": true,
  "field_mapping": {{
    "q1": ["字段1", "字段2"]
  }},
  "common_fields": ["公共条件字段1"]
}}
</result>

用户问题：
{question}

SQL：
{sql_content}

SQL 执行结果字段和样例：
{json.dumps(compact_result, ensure_ascii=False)}
"""

    # ==================== 可移除全局数量字段分析 ====================

    @staticmethod
    def build_removable_quantity_field_prompt(
        question: str, sql_content: str, compact_result: dict[str, Any]
    ) -> str:
        """构建可移除数量字段分析提示词"""
        return f"""请基于用户问题、SQL 和 SQL 执行结果字段，判断哪些全局数量字段适合从多行明细中拆出或移除，并判断哪些数量字段是用户明确问到的。

要求：
1、只输出 removable_quantity_fields 和 explicit_quantity_fields，不输出其他字段。
2、removable_quantity_fields 表示按完整问题口径计算出的全局总数或全局汇总数量字段，适合从多行明细中拆出或移除。
3、explicit_quantity_fields 表示用户问题中明确询问的数量字段，例如用户问“有多少、多少个、几次、数量、总数、存量、累计”等，且该字段正是对这个数量问题的回答。
4、如果数量字段只是 SQL 为了补充证据、辅助判断或关联明细而带出的字段，用户并没有明确询问该数量，则不能列入 explicit_quantity_fields，但如果符合可移除条件，可以列入 removable_quantity_fields。
5、只有当字段是全局汇总数量，并且明细行已经提供分布维度、状态、时间、类型等来源或证据字段时，才可列入 removable_quantity_fields。
6、分布明细数量、每个分布对象自己的数量、每行不同的数量不能列入 removable_quantity_fields。
7、编码/编号类字段、原始测量值、差值、阈值、时间字段不能列入 removable_quantity_fields 或 explicit_quantity_fields。
8、explicit_quantity_fields 必须是 removable_quantity_fields 的子集；如果用户没有明确问数量，explicit_quantity_fields 输出空列表。
9、如果没有可靠可移除的全局数量字段，removable_quantity_fields 输出空列表。
10、只允许输出 JSON，且必须放在<result></result>标签中。

输出 JSON 格式：
<result>
{{
  "removable_quantity_fields": ["可移除全局数量字段1"],
  "explicit_quantity_fields": ["用户明确问到的全局数量字段1"]
}}
</result>

用户问题：
{question}

SQL：
{sql_content}

SQL 执行结果字段和样例：
{json.dumps(compact_result, ensure_ascii=False)}
"""

    # ==================== 结果格式化 ====================

    @staticmethod
    def build_result_format_prompt(question: str, data: list[dict], sql: str) -> str:
        """构建结果格式化提示词"""
        data_str = ""
        if data:
            data_str = str(data)

        return f"""### 指令
            根据用户query以及返回结果信息判断是否存在符合用户意图的绘图数据，并生成结果摘要，需满足以下要求：
            1、最终展示主体默认是文字结果，"type"字段固定输出"text"。
            2、"figure_type"字段表示可选绘图类型，取值只能是pie(饼状图), bar(柱状图), line(曲线图), text(不绘图)。
            3、SQL结果中可能包含大量证据字段、判断字段或辅助字段，不能因为全量字段超过两列就直接放弃绘图；需要从全量字段中寻找最符合用户意图的两列作为绘图数据。
            4、绘图仅支持两列："dimensions"为x轴或分类维度字段，"metrics"为y轴或度量字段；二者都必须是结果数据中真实存在的字段名，且只能各输出一个字段名。
            5、如果无法从结果字段中找到一组能表达用户核心意图的维度和指标，"figure_type"输出"text"，"dimensions"和"metrics"输出空字符串。
            6、柱状图、折线图一般用于数量、趋势、排名、分布等可比较数据；折线图优先用于时间序列；饼状图只用于用户明确询问占比、构成、分布占比的情况；其他情况使用"text"。
            7、"title"字段对应用户统计信息。
            8、"content_desc"字段对应结果数据的分析内容，需要结合证据字段自然说明查询结论，无需对数据进行加减乘除等二次处理，此部分字数控制在200字以内。
            9、结果直接返回json字符串，不用markdown形式。
            10、将结果放到<result></result>标签中，并且换行输出。

            ### 用户query
            {question}

            ### sql
            {sql}

            ### 结果数据
            {data_str}

            ### 示例
            曲线图：
            <result>{{"type":"text","figure_type":"line","title":"近一年按月销售额统计","dimensions":"sale_month","metrics":"sale_amount","content_desc":"从结果可以看出，销售额在不同月份有明显波动，其中3月和8月明显高于其他月份。"}}</result>

            文字：
            <result>{{"type":"text","figure_type":"text","title":"近一年订单数统计","dimensions":"","metrics":"","content_desc":"统计期间内共产生订单150笔，主要集中在A类和B类两大类，其中A类占比最高，占60.23%。"}}</result>

            饼状图：
            <result>{{"type":"text","figure_type":"pie","title":"近一年各品类销售额占比分布","dimensions":"category","metrics":"sale_amount","content_desc":"从结果可以看出，不同品类的销售分布相对不均，A类和B类占比较高。"}}</result>

            柱状图:
            <result>{{"type":"text","figure_type":"bar","title":"近一年按月订单数统计","dimensions":"order_month","metrics":"order_count","content_desc":"结果显示了各月份的订单数量，3月和8月的订单数明显高于其他月份。"}}</result>
            /no_think
        """

    # ==================== 空结果友好提示 ====================

    @staticmethod
    def build_empty_result_rewrite_prompt(question: str) -> str:
        """构建空结果友好提示词"""
        return f"""### 任务
            用户的问题已经完成数据库查询，但查询结果为空。请根据用户原始问题，生成一句自然、友好的提示，明确说明数据库中无相关数据。

            ### 要求
            1、需要紧扣用户问题中的查询对象、时间、地点、业务条件或指标。
            2、不要说 SQL、数据库表、字段、技术细节。
            3、不要编造原因，不要猜测数据缺失原因。
            4、语气要自然，适合直接展示给用户。
            5、只输出 JSON，并放在<result></result>标签中。
            6、JSON 中只允许包含 result_desc 一个字段。
            7、result_desc 必须明确表达数据库中缺少对应信息，优先使用“数据库中暂无XXX的相关信息”的句式。
            8、对于“是否、有无、有没有、是不是、采用的是不是”等判断类问题，需要同时保留查询对象和判断点。
            9、对于“多少、数量、几次、几个、存量、总数、累计”等数量类问题，数量通常是由数据库记录统计得到的，不要表达为“暂无数量/数字信息”，应表达为数据库中暂无满足该查询条件的相关记录或数据。

            ### 用户问题
            {question}

            ### 输出格式
            <result>{{"result_desc":"数据库中暂无该问题的相关信息。"}}
            </result>

            ### 示例
            用户问题：甲公司去年采用的是自研系统吗？
            <result>{{"result_desc":"数据库中暂无甲公司去年是否采用自研系统的相关信息。"}}
            </result>

            用户问题：乙产品过去一年被退货了几次？
            <result>{{"result_desc":"数据库中暂无乙产品过去一年退货的相关记录。"}}
            </result>
            /no_think
        """
