"""Stage-3 tests: chunker (pure functions) + parser_client (local formats + faked MinerU).

全部 hermetic：MinerU 用 monkeypatch 的 requests.post，LibreOffice 不真跑。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.deployment_config import KnowledgeDeploymentConfig
from server.knowledge.chunker import chunk_document, count_tokens
from server.knowledge.parser_client import (
    ParseError,
    _extract_mineru_payload,
    _markdown_to_content_list,
    parse_document,
)


# ------------------------------------------------------------------ chunker


def _text_item(text: str, level: int | None = None) -> dict:
    item = {"type": "text", "text": text}
    if level is not None:
        item["text_level"] = level
    return item


def test_chunker_groups_sections_by_headings() -> None:
    # 正文需要 >50 token，否则会被尾块合并规则并掉（见 _merge_short_tails）
    body1 = "这是第一章的正文内容，描述总则条款的适用范围、术语定义与基本要求。" * 3
    body2 = "本规范适用于所有氢电解决方案的设计、评审、交付与验收的全流程管理。" * 3
    body3 = "第二章正文，介绍系统设计的总体原则、接口划分与关键参数选取方法。" * 3
    content_list = [
        _text_item("第一章 总则", level=1),
        _text_item(body1),
        _text_item("1.1 适用范围", level=2),
        _text_item(body2),
        _text_item("第二章 设计", level=1),
        _text_item(body3),
    ]
    chunks = chunk_document(content_list, chunk_size=400)

    assert len(chunks) >= 2
    assert chunks[0].chunk_title.startswith("第一章 总则")
    assert chunks[0].doc_pos == 0
    assert [c.doc_pos for c in chunks] == list(range(len(chunks)))
    assert all(c.token_num > 0 for c in chunks)
    # 二级标题路径：父级标题保留在 chunk_title 里
    scoped = [c for c in chunks if "1.1 适用范围" in c.chunk_title]
    assert scoped and "第一章 总则" in scoped[0].chunk_title
    # 新的一级标题重置标题路径
    chapter2 = [c for c in chunks if c.chunk_title.startswith("第二章")]
    assert chapter2 and "第一章" not in chapter2[0].chunk_title


def test_chunker_recursive_split_respects_size_and_overlap() -> None:
    # 构造一个远超 chunk_size 的单 section：20 段，每段 ~60 个汉字
    paragraph = "氢燃料电池系统的热管理设计需要综合考虑电堆散热、空压机冷却与整车回路的耦合。" * 2
    content_list = [_text_item("\n\n".join(paragraph for _ in range(20)))]
    size, overlap = 200, 32
    chunks = chunk_document(content_list, chunk_size=size, chunk_overlap=overlap)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_num <= size + overlap + 16  # 重叠前缀 + 打包余量
    # 重叠生效：相邻块结尾/开头有公共 token 序列
    tail = chunks[0].content[-10:]
    assert tail in chunks[1].content


def test_chunker_merges_short_tail_into_previous() -> None:
    long_paragraph = "这是一个足够长的段落，用来撑满一个分块的容量限制。" * 30
    content_list = [_text_item(f"{long_paragraph}\n\n短尾。")]
    chunks = chunk_document(content_list, chunk_size=200, chunk_overlap=0)

    # "短尾。" 只有几个 token，应被并入前一块，而不是独立成块
    assert chunks[-1].content.endswith("短尾。")
    assert all(count_tokens(c.content) >= 10 for c in chunks)


def test_chunker_min_tail_tokens_configurable() -> None:
    # 用标题把短尾隔成独立 section，保证它分块后一定是单独的块（而非被尺寸打包吸走）
    content_list = [
        _text_item("这是第一节的正文内容，长度足够独立成块不会被合并掉。" * 3),
        _text_item("第二节", level=1),
        _text_item("短尾。"),
    ]

    # min_tail_tokens=0 关闭合并："短尾。" 作为第二节的块独立存活
    chunks = chunk_document(content_list, chunk_size=200, chunk_overlap=0, min_tail_tokens=0)
    assert chunks[-1].content == "短尾。"
    assert chunks[-1].chunk_title == "第二节"

    # 默认 50 时同一输入的短尾被并掉（行为不随新参数回归）
    merged = chunk_document(content_list, chunk_size=200, chunk_overlap=0)
    assert len(merged) == len(chunks) - 1
    assert merged[-1].content.endswith("短尾。")


def test_chunker_table_whole_block_and_oversized_row_split() -> None:
    header = "<tr><td>型号</td><td>功率</td></tr>"
    rows = "".join(f"<tr><td>H{i:03d}</td><td>{i}kW</td></tr>" for i in range(5))
    small = {"type": "table", "table_caption": ["产品参数表"], "table_body": f"<table>{header}{rows}</table>"}

    chunks = chunk_document([small], chunk_size=400)
    assert len(chunks) == 1
    assert chunks[0].chunk_title.endswith("（表）")
    assert "产品参数表" in chunks[0].content

    # 超大表：按行切分且每块重复表头
    big_rows = "".join(
        f"<tr><td>型号H{i:04d}</td><td>{'很长的规格描述' * 20}</td></tr>" for i in range(30)
    )
    big = {"type": "table", "table_caption": [], "table_body": f"<table>{header}{big_rows}</table>"}
    big_chunks = chunk_document([big], chunk_size=200)
    assert len(big_chunks) > 1
    for chunk in big_chunks:
        assert "型号" in chunk.content  # 表头重复
        assert "（表）" in chunk.chunk_title


def test_chunker_skips_page_noise_and_keeps_image_caption() -> None:
    content_list = [
        {"type": "page_number", "text": "12"},
        {"type": "page_footnote", "text": "注：内部资料"},
        {"type": "image", "img_caption": ["图3-1 系统拓扑图"]},
        {"type": "image", "img_caption": []},  # 无 caption 的图片整块丢弃
        _text_item("正文段落内容，长度足够避免被尾块合并吞噬。" * 3),
    ]
    chunks = chunk_document(content_list)
    combined = "\n".join(c.content for c in chunks)
    assert "12" not in combined
    assert "内部资料" not in combined
    assert "图3-1 系统拓扑图" in combined


# ------------------------------------------------------------- parser：本地


def test_parse_txt_splits_paragraphs(tmp_path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("第一段内容。\n\n第二段内容。\n\n\n第三段。", encoding="utf-8")
    parsed = parse_document(path, ".txt", KnowledgeDeploymentConfig())

    assert parsed.parser == "local"
    assert [item["text"] for item in parsed.content_list] == [
        "第一段内容。",
        "第二段内容。",
        "第三段。",
    ]
    assert parsed.content_md.startswith("第一段")


def test_parse_md_maps_heading_levels(tmp_path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("# 一级标题\n\n正文一。\n\n## 二级标题\n正文二。\n", encoding="utf-8")
    parsed = parse_document(path, ".md", KnowledgeDeploymentConfig())

    levels = [(i.get("text_level"), i["text"]) for i in parsed.content_list]
    assert levels[0] == (1, "一级标题")
    assert levels[1] == (None, "正文一。")
    assert levels[2] == (2, "二级标题")
    assert levels[3] == (None, "正文二。")


def test_markdown_heading_edge_cases() -> None:
    items = _markdown_to_content_list("#无空格不是标题\n####### 七级也不是\n# 正常\n")
    assert items[0] == {"type": "text", "text": "#无空格不是标题\n####### 七级也不是"}
    assert items[1]["text_level"] == 1


def test_parse_xlsx_builds_html_tables(tmp_path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "参数"
    sheet.append(["型号", "功率"])
    sheet.append(["H100", "100kW"])
    sheet.append([None, None])  # 空行被过滤
    path = tmp_path / "spec.xlsx"
    workbook.save(path)

    parsed = parse_document(path, ".xlsx", KnowledgeDeploymentConfig())

    assert parsed.parser == "local"
    assert len(parsed.content_list) == 1
    item = parsed.content_list[0]
    assert item["type"] == "table"
    assert item["table_caption"] == ["参数"]
    assert "<td>H100</td>" in item["table_body"]
    assert "<td>None</td>" not in item["table_body"]
    assert "| H100 | 100kW |" in parsed.content_md


def test_parse_unsupported_ext_raises(tmp_path) -> None:
    path = tmp_path / "page.html"
    path.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.raises(ParseError, match="不支持的文件格式"):
        parse_document(path, ".html", KnowledgeDeploymentConfig())


# ---------------------------------------------------------- parser：MinerU


class _FakeMineruResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _mineru_config() -> KnowledgeDeploymentConfig:
    return KnowledgeDeploymentConfig(enabled=True, mineru_url="http://gpu-server:18888/")


def test_parse_pdf_posts_to_mineru_and_extracts_nested_results(tmp_path, monkeypatch) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    captured = {}

    def fake_post(url, files=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["filename"] = files["file"][0]
        captured["timeout"] = timeout
        return _FakeMineruResponse(
            {"results": {"paper": {"md_content": "# 标题", "content_list": [{"type": "text", "text": "正文"}]}}}
        )

    monkeypatch.setattr("server.knowledge.parser_client.requests.post", fake_post)
    parsed = parse_document(path, ".pdf", _mineru_config())

    assert captured["url"] == "http://gpu-server:18888/file_parse"  # 尾斜杠被剥掉
    assert captured["filename"] == "paper.pdf"
    assert parsed.parser == "mineru"
    assert parsed.content_md == "# 标题"
    assert parsed.content_list[0]["text"] == "正文"


def test_parse_pdf_flat_response_shape(tmp_path, monkeypatch) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF fake")
    monkeypatch.setattr(
        "server.knowledge.parser_client.requests.post",
        lambda url, **kw: _FakeMineruResponse({"md_content": "m", "content_list": []}),
    )
    parsed = parse_document(path, ".pdf", _mineru_config())
    assert parsed.content_md == "m"


def test_parse_pdf_http_error_and_bad_payload_raise(tmp_path, monkeypatch) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF fake")

    monkeypatch.setattr(
        "server.knowledge.parser_client.requests.post",
        lambda url, **kw: _FakeMineruResponse({}, status_code=500),
    )
    with pytest.raises(ParseError, match="MinerU 解析接口调用失败"):
        parse_document(path, ".pdf", _mineru_config())

    monkeypatch.setattr(
        "server.knowledge.parser_client.requests.post",
        lambda url, **kw: _FakeMineruResponse({"unexpected": True}),
    )
    with pytest.raises(ParseError, match="content_list"):
        parse_document(path, ".pdf", _mineru_config())


def test_parse_pdf_requires_mineru_url(tmp_path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF fake")
    with pytest.raises(ParseError, match="mineru_url 未配置"):
        parse_document(path, ".pdf", KnowledgeDeploymentConfig(enabled=True))


def test_parse_docx_converts_via_soffice_then_mineru(tmp_path, monkeypatch) -> None:
    path = tmp_path / "报告.docx"
    path.write_bytes(b"fake-docx")
    calls = {"soffice": None, "mineru": None}

    def fake_convert(p, config):
        calls["soffice"] = p.name
        pdf = tmp_path / "converted.pdf"
        pdf.write_bytes(b"%PDF converted")
        return pdf

    def fake_mineru(pdf_path, config):
        calls["mineru"] = pdf_path.name
        from server.knowledge.parser_client import ParsedDoc

        return ParsedDoc(content_md="m", content_list=[{"type": "text", "text": "t"}], parser="mineru")

    monkeypatch.setattr("server.knowledge.parser_client._convert_to_pdf", fake_convert)
    monkeypatch.setattr("server.knowledge.parser_client._parse_with_mineru", fake_mineru)

    parsed = parse_document(path, ".docx", _mineru_config())

    assert calls == {"soffice": "报告.docx", "mineru": "converted.pdf"}
    assert parsed.parser == "mineru"


def test_parse_office_moves_preview_pdf_to_dest(tmp_path, monkeypatch) -> None:
    """传 preview_dest：转换 PDF 挪作预览件，转换临时目录整体清理。"""
    path = tmp_path / "报告.docx"
    path.write_bytes(b"fake-docx")
    conv_dir = tmp_path / "hermes-kb-office-test"  # 与 _convert_to_pdf 的 mkdtemp 前缀约定一致
    conv_dir.mkdir()

    def fake_convert(p, config):
        pdf = conv_dir / "报告.pdf"
        pdf.write_bytes(b"%PDF converted")
        (conv_dir / "lo-profile").mkdir()  # 隔离 profile 目录也应被清掉
        return pdf

    from server.knowledge.parser_client import ParsedDoc

    monkeypatch.setattr("server.knowledge.parser_client._convert_to_pdf", fake_convert)
    monkeypatch.setattr(
        "server.knowledge.parser_client._parse_with_mineru",
        lambda pdf_path, config: ParsedDoc(
            content_md="m", content_list=[{"type": "text", "text": "t"}], parser="mineru"
        ),
    )

    preview_dest = tmp_path / "files" / "hash123.pdf"
    parse_document(path, ".docx", _mineru_config(), preview_dest=preview_dest)

    assert preview_dest.read_bytes() == b"%PDF converted"
    assert not conv_dir.exists()  # 临时目录（含 profile）已清理


def test_parse_office_keeps_preview_even_when_mineru_fails(tmp_path, monkeypatch) -> None:
    """MinerU 失败时预览件仍保留（PDF 本身有效），异常照常抛出。"""
    path = tmp_path / "报告.docx"
    path.write_bytes(b"fake-docx")

    def fake_convert(p, config):
        pdf = tmp_path / "conv.pdf"
        pdf.write_bytes(b"%PDF converted")
        return pdf

    monkeypatch.setattr("server.knowledge.parser_client._convert_to_pdf", fake_convert)

    def boom(pdf_path, config):
        raise ParseError("MinerU down")

    monkeypatch.setattr("server.knowledge.parser_client._parse_with_mineru", boom)

    preview_dest = tmp_path / "files" / "hash123.pdf"
    with pytest.raises(ParseError, match="MinerU down"):
        parse_document(path, ".docx", _mineru_config(), preview_dest=preview_dest)
    assert preview_dest.read_bytes() == b"%PDF converted"


def test_soffice_missing_raises_parse_error(tmp_path, monkeypatch) -> None:
    import subprocess as sp

    monkeypatch.setattr(
        sp, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("soffice"))
    )
    from server.knowledge.parser_client import _convert_to_pdf

    path = tmp_path / "a.docx"
    path.write_bytes(b"x")
    with pytest.raises(ParseError, match="LibreOffice"):
        _convert_to_pdf(path, KnowledgeDeploymentConfig(enabled=True))


def test_convert_to_pdf_uses_configured_soffice_path(tmp_path, monkeypatch) -> None:
    """knowledge.soffice_path 配置后作为 argv[0] 调用（Windows 上 LibreOffice 不在 PATH）。"""
    import subprocess as sp

    seen: dict[str, str] = {}

    def fake_run(argv, *a, **kw):
        seen["exe"] = argv[0]
        out_dir = Path(argv[argv.index("--outdir") + 1])
        (out_dir / "a.pdf").write_bytes(b"%PDF")
        return sp.CompletedProcess(argv, 0)

    monkeypatch.setattr(sp, "run", fake_run)
    from server.knowledge.parser_client import _convert_to_pdf

    path = tmp_path / "a.docx"
    path.write_bytes(b"x")
    cfg = KnowledgeDeploymentConfig(
        enabled=True, soffice_path=r"F:\软件安装\LibreOffice\program\soffice.exe"
    )
    pdf = _convert_to_pdf(path, cfg)

    assert seen["exe"] == r"F:\软件安装\LibreOffice\program\soffice.exe"
    assert pdf.name == "a.pdf"


def test_convert_to_pdf_falls_back_to_path_soffice(tmp_path, monkeypatch) -> None:
    """未配置 soffice_path 时回退 PATH 里的 soffice。"""
    import subprocess as sp

    seen: dict[str, str] = {}

    def fake_run(argv, *a, **kw):
        seen["exe"] = argv[0]
        out_dir = Path(argv[argv.index("--outdir") + 1])
        (out_dir / "a.pdf").write_bytes(b"%PDF")
        return sp.CompletedProcess(argv, 0)

    monkeypatch.setattr(sp, "run", fake_run)
    from server.knowledge.parser_client import _convert_to_pdf

    path = tmp_path / "a.docx"
    path.write_bytes(b"x")
    _convert_to_pdf(path, KnowledgeDeploymentConfig(enabled=True))

    assert seen["exe"] == "soffice"


def test_extract_mineru_payload_rejects_non_dict_items() -> None:
    response = _FakeMineruResponse(
        {"md_content": "m", "content_list": [{"type": "text"}, "garbage", None]}
    )
    md, items = _extract_mineru_payload(response)
    assert md == "m"
    assert items == [{"type": "text"}]
