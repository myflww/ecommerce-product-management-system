import io
import zipfile
import zlib

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter, get_configuration
from pypdf.generic import DictionaryObject, EncodedStreamObject, DecodedStreamObject, NameObject

import chat_attachments as attachments


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in entries.items():
            target.writestr(name, content)
    return stream.getvalue()


def docx(text="商品名称：蜂蜡蜡烛", *, extra=None):
    entries = {"word/document.xml": f'<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>规格：6件套</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'}
    entries.update(extra or {})
    return archive(entries)


def xlsx(*, sheets=1, extra_rows="", extra_cells="", shared="00123", formula_value="8"):
    entries = {
        "xl/workbook.xml": '<workbook xmlns="urn:sheet" xmlns:r="urn:relations"><sheets>' + "".join(f'<sheet name="商品表{i}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, sheets + 1)) + '</sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships>' + "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, sheets + 1)) + '</Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="urn:sheet"><si><t>{shared}</t></si></sst>',
    }
    for i in range(1, sheets + 1):
        entries[f"xl/worksheets/sheet{i}.xml"] = f'''<worksheet xmlns="urn:sheet"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>蜂蜡蜡烛</t></is></c><c r="C1"><f>WEBSERVICE("https://should-never-run.example")</f><v>{formula_value}</v></c>{extra_cells}</row>{extra_rows}</sheetData></worksheet>'''
    return archive(entries)


def pdf(*, pages=1, text=True, encrypted=False, compressed_bomb=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        if text or compressed_bomb:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            if compressed_bomb:
                stream = EncodedStreamObject()
                stream._data = zlib.compress(b" " * 2_100_000)
                stream[NameObject("/Filter")] = NameObject("/FlateDecode")
            else:
                stream = DecodedStreamObject()
                stream.set_data(b"BT /F1 12 Tf 10 100 Td (Product: beeswax candles, SKU 00123) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("not-a-real-document-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("name,content,expected", [
    ("商品.TXT", "品名：蜂蜡蜡烛\r\n规格：6件套".encode(), "品名：蜂蜡蜡烛\n规格：6件套"),
    ("文档.md", "# 上品创意\n春季礼盒".encode("gb18030"), "# 上品创意\n春季礼盒"),
    ("表格.csv", "SKU,价格\n00123,15".encode("utf-8-sig"), "SKU,价格\n00123,15"),
    ("数据.json", '{"SKU":"00123"}'.encode(), '{"SKU":"00123"}'),
    ("UTF16.txt", "产品规格清单".encode("utf-16"), "产品规格清单"),
])
def test_plain_document_encodings_and_whitespace(name, content, expected):
    result = attachments.parse_document(name, content)
    assert result == {"name": name, "kind": "document", "text": expected, "characters": len(expected), "truncated": False}


def test_docx_paragraphs_and_table_text_without_embedded_images():
    result = attachments.parse_document("创意.docx", docx(extra={"word/media/image1.png": b"not-read-or-sent"}))
    assert result["text"] == "商品名称：蜂蜡蜡烛\n规格：6件套"
    assert not result["truncated"]
    assert "not-read-or-sent" not in result["text"]


def test_xlsx_shared_strings_preserve_sku_and_no_formula_execution():
    result = attachments.parse_document("商品.xlsx", xlsx())
    assert "00123\t蜂蜡蜡烛\t[公式缓存值] 8" in result["text"]
    assert "商品表1" in result["text"]
    assert "WEBSERVICE" not in result["text"]
    assert not result["truncated"]


def test_xlsx_formula_without_cache_and_bounds():
    extra_rows = "".join(f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>第{i}行</t></is></c></row>' for i in range(2, 203))
    result = attachments.parse_document("限量.xlsx", xlsx(sheets=4, formula_value="", extra_rows=extra_rows, extra_cells='<c r="AO1"><v>DO-NOT-SEND</v></c>'))
    assert "[公式未计算]" in result["text"]
    assert "商品表3" in result["text"] and "商品表4" not in result["text"]
    assert "第200行" in result["text"] and "第201行" not in result["text"]
    assert "DO-NOT-SEND" not in result["text"]
    assert result["truncated"]


def test_pdf_extracts_actual_text_and_restores_configuration():
    original = get_configuration()
    result = attachments.parse_document("catalog.pdf", pdf())
    assert "Product: beeswax candles, SKU 00123" in result["text"]
    assert "第 1 页" in result["text"]
    assert not result["truncated"]
    assert get_configuration() is original


@pytest.mark.parametrize("name,data,part", [
    ("empty.txt", b"", "为空"),
    ("blank.txt", b" \r\n ", "没有可提取"),
    ("binary.txt", b"text\x00binary", "不是可读取"),
    ("data.json", b"{bad json", "JSON"),
    ("old.doc", b"not-supported", "旧版"),
    ("broken.pdf", b"not a pdf", "不是有效"),
    ("broken.docx", b"not a zip", "无法安全读取"),
    ("missing.docx", archive({"word/other.xml": "<p/>"}), "无法安全读取"),
])
def test_invalid_files_are_actionable_400(name, data, part):
    with pytest.raises(HTTPException) as error:
        attachments.parse_document(name, data)
    assert error.value.status_code == 400
    assert part in error.value.detail


def test_name_is_sanitized_and_paths_never_retained():
    result = attachments.parse_document("C:\\private\\secret\u202e.txt", b"hello")
    assert result["name"] == "secret.txt"
    assert attachments.safe_filename("a/b/\x00\n") == "附件"


def test_text_truncation_is_explicit_and_bounded():
    result = attachments.parse_document("large.md", ("资料" * 9000).encode())
    assert len(result["text"]) == attachments.MAX_TEXT_CHARACTERS
    assert result["characters"] == attachments.MAX_TEXT_CHARACTERS
    assert result["truncated"]


def test_file_size_limit_before_decoding(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_FILE_BYTES", 20)
    with pytest.raises(HTTPException, match="10 MB"):
        attachments.parse_document("big.txt", b"X" * 21)


@pytest.mark.parametrize("content", [
    docx(extra={"../escape.txt": "must not extract"}),
    docx(extra={"high-ratio.txt": "X" * 1_100_000}),
    archive({"word/document.xml": '<!DOCTYPE a [<!ENTITY x "secret">]><a>&x;</a>'}),
    archive({"word/document.xml": '<!DOCTYPE a [<!ENTITY x "secret">]><a>&x;</a>'.encode("utf-16")}),
])
def test_office_archive_and_entities_rejected(content):
    with pytest.raises(HTTPException) as error:
        attachments.parse_document("unsafe.docx", content)
    assert error.value.status_code == 400


def test_archive_uncompressed_budget(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_ZIP_BYTES", 20)
    with pytest.raises(HTTPException, match="解压"):
        attachments.parse_document("budget.docx", docx())


def test_xlsx_invalid_shared_string_rejected():
    data = xlsx()
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["xl/worksheets/sheet1.xml"] = entries["xl/worksheets/sheet1.xml"].replace(b"<v>0</v>", b"<v>-1</v>")
    with pytest.raises(HTTPException, match="索引异常"):
        attachments.parse_document("invalid.xlsx", archive(entries))


def test_empty_xlsx_does_not_treat_sheet_name_as_document_contents():
    data = xlsx()
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["xl/worksheets/sheet1.xml"] = '<worksheet xmlns="urn:sheet"><sheetData/></worksheet>'
    with pytest.raises(HTTPException, match="没有可提取"):
        attachments.parse_document("empty.xlsx", archive(entries))


def test_pdf_encrypted_rejected():
    with pytest.raises(HTTPException, match="加密"):
        attachments.parse_document("encrypted.pdf", pdf(encrypted=True))


def test_pdf_scanned_or_blank_explicit_message():
    with pytest.raises(HTTPException, match="扫描件"):
        attachments.parse_document("scan.pdf", pdf(text=False))


def test_pdf_page_limit():
    with pytest.raises(HTTPException, match="30 页"):
        attachments.parse_document("many-pages.pdf", pdf(pages=31))


def test_pdf_compressed_content_limit_does_not_escape_or_change_global_config():
    original = get_configuration()
    with pytest.raises(HTTPException) as error:
        attachments.parse_document("compressed.pdf", pdf(compressed_bomb=True))
    assert error.value.status_code == 400
    assert get_configuration() is original
