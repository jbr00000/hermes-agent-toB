"""Document parsing entry: route each file type to the right parser.

- pdf            → customer-hosted MinerU HTTP API（GPU 服务器，版面/公式/OCR）
- doc/docx/ppt/pptx/xls → LibreOffice headless 转 PDF → MinerU
- txt/md         → 本地直读（md 的 # 层级映射 text_level；转换反而丢结构）
- xlsx           → openpyxl 直读转 HTML 表格（LibreOffice 转 PDF 会按纸张截断宽表）

MinerU 响应约定（客户端容错提取两种形状）：
    {"md_content": "...", "content_list": [...]}
或  {"results": {"<name>": {"md_content": "...", "content_list": [...]}}}
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import logging
from pathlib import Path
import subprocess
import tempfile
from typing import Any

import requests

from server.deployment_config import KnowledgeDeploymentConfig

from . import KnowledgeError

logger = logging.getLogger(__name__)

MINERU_EXTS = {".pdf"}
OFFICE_TO_PDF_EXTS = {".doc", ".docx", ".ppt", ".pptx", ".xls"}
TEXT_EXTS = {".txt", ".md"}
XLSX_EXTS = {".xlsx"}
SUPPORTED_EXTS = MINERU_EXTS | OFFICE_TO_PDF_EXTS | TEXT_EXTS | XLSX_EXTS

_MINERU_TIMEOUT = 600  # GPU 解析大 PDF 可能数分钟
_SOFFICE_TIMEOUT = 300


class ParseError(KnowledgeError):
    """文档解析失败（MinerU/LibreOffice/本地读取任一环节）。"""


@dataclass
class ParsedDoc:
    """统一的解析产物：markdown 全文 + MinerU 风格的 content_list。"""

    content_md: str
    content_list: list[dict[str, Any]] = field(default_factory=list)
    parser: str = "local"


def parse_document(path: Path, file_ext: str, config: KnowledgeDeploymentConfig) -> ParsedDoc:
    """Parse one file into a ParsedDoc. ``file_ext`` is lowercase, with the dot."""
    ext = file_ext.lower()
    if ext in TEXT_EXTS:
        return _parse_text(path, ext)
    if ext in XLSX_EXTS:
        return _parse_xlsx(path)
    if ext in MINERU_EXTS:
        return _parse_with_mineru(path, config)
    if ext in OFFICE_TO_PDF_EXTS:
        pdf_path = _convert_to_pdf(path)
        try:
            return _parse_with_mineru(pdf_path, config)
        finally:
            pdf_path.unlink(missing_ok=True)
    raise ParseError(f"不支持的文件格式: {ext}（支持 {sorted(SUPPORTED_EXTS)}）")


# ------------------------------------------------------------------- MinerU


def _parse_with_mineru(pdf_path: Path, config: KnowledgeDeploymentConfig) -> ParsedDoc:
    if not config.mineru_url:
        raise ParseError("knowledge.mineru_url 未配置，无法解析 PDF/Office 文档")
    url = f"{config.mineru_url.rstrip('/')}/file_parse"
    try:
        with pdf_path.open("rb") as fh:
            response = requests.post(
                url,
                files={"file": (pdf_path.name, fh, "application/pdf")},
                timeout=_MINERU_TIMEOUT,
            )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ParseError(f"MinerU 解析接口调用失败: {exc}") from exc
    md_content, content_list = _extract_mineru_payload(response)
    return ParsedDoc(content_md=md_content, content_list=content_list, parser="mineru")


def _extract_mineru_payload(response: requests.Response) -> tuple[str, list[dict[str, Any]]]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ParseError("MinerU 返回的不是 JSON") from exc
    candidates: list[Any] = [payload]
    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, dict):
        candidates.extend(results.values())
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content_list = candidate.get("content_list")
        if isinstance(content_list, list):
            md_content = candidate.get("md_content") or candidate.get("markdown") or ""
            return str(md_content), [item for item in content_list if isinstance(item, dict)]
    raise ParseError("MinerU 响应中找不到 content_list（响应结构与约定不符）")


def _convert_to_pdf(path: Path) -> Path:
    """LibreOffice headless 转 PDF，返回临时 PDF 路径（调用方负责删除）。"""
    out_dir = Path(tempfile.mkdtemp(prefix="hermes-kb-office-"))
    try:
        result = subprocess.run(
            [
                "soffice",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(path),
            ],
            capture_output=True,
            timeout=_SOFFICE_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ParseError("soffice（LibreOffice）不可用，无法转换 Office 文档") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParseError(f"LibreOffice 转换超时（{_SOFFICE_TIMEOUT}s）: {path.name}") from exc
    pdf_path = out_dir / f"{path.stem}.pdf"
    if result.returncode != 0 or not pdf_path.exists():
        detail = result.stderr.decode("utf-8", errors="replace")[:500]
        raise ParseError(f"LibreOffice 转换失败: {path.name}: {detail}")
    return pdf_path


# ------------------------------------------------------------- 本地直读格式


def _parse_text(path: Path, ext: str) -> ParsedDoc:
    text = path.read_text(encoding="utf-8")
    if ext == ".md":
        content_list = _markdown_to_content_list(text)
    else:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        content_list = [{"type": "text", "text": p} for p in paragraphs]
    return ParsedDoc(content_md=text, content_list=content_list, parser="local")


def _markdown_to_content_list(text: str) -> list[dict[str, Any]]:
    """Markdown → MinerU 风格 content_list：# 层级→text_level，其余按块聚合。"""
    items: list[dict[str, Any]] = []
    buffer: list[str] = []

    def flush() -> None:
        block = "\n".join(buffer).strip()
        buffer.clear()
        if block:
            items.append({"type": "text", "text": block})

    for line in text.splitlines():
        stripped = line.strip()
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if stripped.startswith("#") and 1 <= hashes <= 6 and stripped[hashes:].startswith(" "):
            flush()
            items.append(
                {"type": "text", "text": stripped[hashes:].strip(), "text_level": hashes}
            )
        elif stripped == "":
            flush()
        else:
            buffer.append(line)
    flush()
    return items


def _parse_xlsx(path: Path) -> ParsedDoc:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dependency is pinned in pyproject
        raise ParseError("openpyxl 未安装，无法解析 xlsx") from exc
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError(f"xlsx 读取失败: {path.name}: {exc}") from exc

    content_list: list[dict[str, Any]] = []
    md_parts: list[str] = []
    for sheet in workbook.worksheets:
        rows = [["" if cell is None else str(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(cell.strip() for cell in row)]
        if not rows:
            continue
        table_html = _rows_to_html_table(rows)
        content_list.append(
            {"type": "table", "table_caption": [sheet.title], "table_body": table_html}
        )
        md_parts.append(f"## {sheet.title}\n\n{_rows_to_markdown(rows)}")
    workbook.close()
    if not content_list:
        raise ParseError(f"xlsx 没有任何非空 sheet: {path.name}")
    return ParsedDoc(content_md="\n\n".join(md_parts), content_list=content_list, parser="local")


def _rows_to_html_table(rows: list[list[str]]) -> str:
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table>{body}</table>"


def _rows_to_markdown(rows: list[list[str]]) -> str:
    header, *body = rows
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)
