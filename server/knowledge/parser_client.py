"""Document parsing entry: route each file type to the right parser.

- pdf            → customer-hosted MinerU（GPU 服务器，版面/公式/OCR），两种形态：
                   mineru_mode=api → MinerU FastAPI 封装（POST /file_parse）
                   mineru_mode=vlm → vLLM 裸起的 MinerU2.5 VLM（OpenAI 兼容），
                   经 mineru-vl-utils 两段式客户端驱动（本地渲染页图 → 远程识别）
- doc/docx/ppt/pptx/xls → LibreOffice headless 转 PDF → MinerU
- txt/md         → 本地直读（md 的 # 层级映射 text_level；转换反而丢结构）
- xlsx           → openpyxl 直读转 HTML 表格（LibreOffice 转 PDF 会按纸张截断宽表）

MinerU api 模式响应约定（客户端容错提取两种形状）：
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
    if config.mineru_mode == "vlm":
        return _parse_with_mineru_vlm(pdf_path, config)
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


# ------------------------------------------------------- MinerU VLM 模式

_VLM_RENDER_DPI = 200  # MinerU 2.5 官方管线的页面渲染分辨率

# MinerU2.5 块类型 → content_list 映射。标题给 text_level 供 chunker 分段；
# 页眉/页脚/页码由客户端 abandon_paratext 丢弃，图片/图表无可检索文本直接跳过。
_VLM_HEADING_LEVELS = {"doc_title": 1, "title": 1, "paragraph_title": 2}
_VLM_SKIP_TYPES = {"image", "image_block", "chart", "unknown"}
_VLM_EQUATION_TYPES = {"equation", "equation_block"}


def _parse_with_mineru_vlm(pdf_path: Path, config: KnowledgeDeploymentConfig) -> ParsedDoc:
    """vLLM 裸起的 MinerU2.5 VLM：本地把 PDF 渲染成页图，远程两段式识别。"""
    try:
        from loguru import logger as _loguru_logger
        from mineru_vl_utils import MinerUClient
    except ImportError as exc:  # pragma: no cover - dependency is pinned in pyproject
        raise ParseError("mineru-vl-utils 未安装，无法使用 mineru_mode=vlm") from exc
    # mineru-vl-utils 用 loguru 且在 DEBUG 级逐页 dump 版面原始输出，与
    # server 的标准 logging 体系无关——大 PDF 会刷屏，直接禁掉该模块
    _loguru_logger.disable("mineru_vl_utils")
    images = _pdf_to_images(pdf_path)
    client = MinerUClient(
        backend="http-client",
        server_url=config.mineru_url.rstrip("/"),
        # 服务端模型名是部署方自定的（如 vllm-mineru2.5-1.2B），不做名称校验
        skip_model_name_checking=True,
        abandon_paratext=True,
        use_tqdm=False,
        http_timeout=_MINERU_TIMEOUT,
    )
    try:
        results = client.batch_two_step_extract(images)
    except Exception as exc:
        raise ParseError(f"MinerU VLM 解析失败: {exc}") from exc
    content_list = _vlm_results_to_content_list(results)
    if not content_list:
        raise ParseError(f"MinerU VLM 未识别出任何内容: {pdf_path.name}")
    return ParsedDoc(
        content_md=_content_list_to_markdown(content_list),
        content_list=content_list,
        parser="mineru-vlm",
    )


def _pdf_to_images(pdf_path: Path) -> list[Any]:
    """PDF → 每页 PIL 图片（vlm 模式的唯一本地计算）。"""
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependency is pinned in pyproject
        raise ParseError("pymupdf 未安装，无法渲染 PDF 页图") from exc
    from PIL import Image

    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:
        raise ParseError(f"PDF 打开失败: {pdf_path.name}: {exc}") from exc
    images: list[Any] = []
    try:
        for page in document:
            pixmap = page.get_pixmap(dpi=_VLM_RENDER_DPI)
            images.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
    finally:
        document.close()
    if not images:
        raise ParseError(f"PDF 没有可渲染的页面: {pdf_path.name}")
    return images


def _vlm_results_to_content_list(results: list[Any]) -> list[dict[str, Any]]:
    """MinerU2.5 ContentBlock 序列 → MinerU 风格 content_list（与 api 模式同构）。"""
    items: list[dict[str, Any]] = []
    for blocks in results:
        for block in blocks:
            block_type = block.get("type")
            content = str(block.get("content") or "").strip()
            if not content or block_type in _VLM_SKIP_TYPES:
                continue
            if block_type in _VLM_HEADING_LEVELS:
                items.append(
                    {"type": "text", "text": content, "text_level": _VLM_HEADING_LEVELS[block_type]}
                )
            elif block_type == "table":
                items.append({"type": "table", "table_body": content})
            elif block_type in _VLM_EQUATION_TYPES:
                items.append({"type": "text", "text": f"$${content}$$"})
            elif block.get("merge_prev") and items and items[-1].get("type") == "text" and not items[-1].get("text_level"):
                items[-1]["text"] = f"{items[-1]['text']}\n{content}"
            else:
                items.append({"type": "text", "text": content})
    return items


def _content_list_to_markdown(content_list: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in content_list:
        level = item.get("text_level")
        if item.get("type") == "table":
            parts.append(str(item.get("table_body") or ""))
        elif level:
            parts.append(f"{'#' * int(level)} {item.get('text', '')}")
        else:
            parts.append(str(item.get("text") or ""))
    return "\n\n".join(part for part in parts if part)


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
