"""SQL 安全校验 —— 原样移植 lone-ai ``core/sql_utils.py`` 的 SQLValidator。

词边界危险关键词 + 注入特征 + 只允许查询语句开头。这是问数 SQL 执行的
第一道闸（第二道是业务库的只读 GRANT，见 CLAUDE.md 不变量 12）。
"""
from __future__ import annotations

import re


class SQLValidator:
    """SQL 安全检查和验证工具类。"""

    # 危险操作关键词
    DANGEROUS_KEYWORDS = [
        "DROP", "TRUNCATE", "DELETE", "INSERT", "UPDATE",
        "ALTER", "CREATE", "GRANT", "REVOKE",
    ]

    # SQL 注入特征
    INJECTION_PATTERNS = [
        r"--",  # SQL 注释
        r"/\*",  # 多行注释开始
        r"\*/",  # 多行注释结束
        r";\s*\w+",  # 多语句
        r"union\s+select",  # UNION 注入（不区分大小写）
        r"1\s*=\s*1",  # 恒真条件
        r"exec\s*\(",  # 执行命令
    ]

    # 只允许的 SQL 语句开头
    ALLOWED_STATEMENTS = ["SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"]

    @classmethod
    def check_security(cls, sql: str) -> tuple[bool, str | list, list[str]]:
        """检查 SQL 安全性，返回 (passed, blocked_reason, warnings)。"""
        warnings: list[str] = []
        blocked_reason: str | list = []
        passed = True

        sql_upper = sql.upper().strip()

        # 1. 检查是否为允许的语句类型
        if not any(sql_upper.startswith(stmt) for stmt in cls.ALLOWED_STATEMENTS):
            passed = False
            blocked_reason = "仅允许查询语句(SELECT/WITH/SHOW/DESCRIBE/EXPLAIN)，不允许执行其他类型语句"
            return passed, blocked_reason, warnings

        # 2. 检查危险操作
        dangerous_found = cls._check_dangerous_keywords(sql_upper)
        if dangerous_found:
            passed = False
            blocked_reason = f"包含危险操作: {', '.join(dangerous_found)}"
            return passed, blocked_reason, warnings

        # 3. 检查 SQL 注入特征
        injection_found = cls._check_injection_patterns(sql)
        if injection_found:
            passed = False
            blocked_reason = f"疑似SQL注入: {injection_found}"
            return passed, blocked_reason, warnings

        # 4. 基本语法检查
        syntax_warnings = cls._check_syntax(sql)
        if syntax_warnings:
            warnings.extend(syntax_warnings)

        return passed, blocked_reason, warnings

    @classmethod
    def _check_dangerous_keywords(cls, sql_upper: str) -> list[str]:
        """检查危险关键词（单词边界匹配，避免误判）。"""
        found = []
        for keyword in cls.DANGEROUS_KEYWORDS:
            pattern = r"\b" + keyword + r"\b"
            if re.search(pattern, sql_upper):
                found.append(keyword)
        return found

    @classmethod
    def _check_injection_patterns(cls, sql: str) -> str | None:
        """检查 SQL 注入特征。"""
        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, sql, re.IGNORECASE):
                return pattern
        return None

    @classmethod
    def _check_syntax(cls, sql: str) -> list[str]:
        """基本语法检查。"""
        warnings = []

        # 检查括号是否匹配
        if sql.count("(") != sql.count(")"):
            warnings.append("括号不匹配")
            return warnings

        # 检查是否有未闭合的引号
        single_quotes = [m.start() for m in re.finditer(r"'", sql)]
        if len(single_quotes) % 2 != 0:
            warnings.append("单引号不匹配")

        # 检查是否有分号（多语句会被上面的注入检查拦截，这里只是提示）
        if sql.count(";") > 1:
            warnings.append("包含多个分号")

        return warnings
