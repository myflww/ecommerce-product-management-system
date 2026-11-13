"""Read chat documents locally into bounded plain text; never execute or save them."""
from __future__ import annotations

import io
import json
import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from pypdf import PdfReader, apply_configuration
from pypdf.errors import PyPdfError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARACTERS = 16000
MAX_PDF_PAGES = 30
MAX_SHEETS = 3
MAX_ROWS = 200
MAX_COLUMNS = 40
MAX_ZIP_BYTES = 30 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
DOCUMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".xlsx"}


def safe_filename(filename: str) -> str:
    """Remove client-supplied paths and control characters from display names."""
    if not isinstance(filename, str):
        return "附件"
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "", name).strip()
    return name[:180] or "附件"


class _Text:
    def __init__(self):
        self.parts: list[str] = []
        self.size = 0
        self.truncated = False

    def add(self, text: str) -> bool:
        remaining = MAX_TEXT_CHARACTERS - self.size
        if len(text) > remaining:
            self.parts.append(text[:remaining])
            self.size = MAX_TEXT_CHARACTERS
            self.truncated = True
            return False
        self.parts.append(text)
        self.size += len(text)
        return True

    def result(self) -> str:
        return "".join(self.parts).strip()


def _error(message: str):
    raise HTTPException(400, message)


def _text_document(content: bytes, ext: str) -> tuple[str, bool]:
    text = None
    encodings = ("utf-16",) if content.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = content.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None or "\x00" in text or sum(ord(c) < 32 and c not in "\n\r\t" for c in text) > max(1, len(text) // 100):
        _error("文件不是可读取的文字，请使用 UTF-8 或 GB18030 编码的 TXT、MD、CSV 或 JSON。")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if ext == ".json":
        try:
            json.loads(text)
        except (ValueError, RecursionError):
            _error("JSON 文件格式不正确或层级过深，请检查后重新上传。")
    return text[:MAX_TEXT_CHARACTERS], len(text) > MAX_TEXT_CHARACTERS


def _xml(archive: zipfile.ZipFile, name: str):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        _error("文档内部文字数据过大，请拆分文档后上传。")
    data = archive.read(info)
    # Removing NULs also catches declarations encoded as UTF-16/32. ElementTree
    # never fetches external resources, and declarations are rejected entirely.
    normalized = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
        _error("文档包含不支持的 XML 声明，请另存为普通 DOCX 或 XLSX 后上传。")
    return ET.fromstring(data)


def _archive(content: bytes) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(content))
    try:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(infos) > 1500 or len(names) != len(set(names)) or sum(info.file_size for info in infos) > MAX_ZIP_BYTES:
            _error("文档解压后的内容过大或结构异常，请拆分或另存后上传。")
        for info in infos:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename or ":" in info.filename or info.flag_bits & 1:
                _error("文档的压缩结构异常或已加密，请另存为普通文档后上传。")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                _error("文档使用了不支持的压缩格式，请重新另存为 DOCX 或 XLSX。")
            if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * 300:
                _error("文档压缩比例异常，请拆分文档后上传。")
        return archive
    except Exception:
        archive.close()
        raise


def _docx(content: bytes) -> tuple[str, bool]:
    text = _Text()
    with _archive(content) as archive:
        root = _xml(archive, "word/document.xml")
        for paragraph in root.iter():
            if paragraph.tag.rsplit("}", 1)[-1] != "p":
                continue
            pieces = []
            for element in paragraph.iter():
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "t":
                    pieces.append(element.text or "")
                elif tag == "tab":
                    pieces.append("\t")
                elif tag in {"br", "cr"}:
                    pieces.append("\n")
            if not text.add("".join(pieces) + "\n"):
                break
    return text.result(), text.truncated


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Za-z]{1,3}", reference)
    if not letters:
        return 0
    value = 0
    for letter in letters.group().upper():
        value = value * 26 + ord(letter) - 64
    return value


def _xlsx(content: bytes) -> tuple[str, bool]:
    text = _Text()
    has_data = False
    with _archive(content) as archive:
        workbook = _xml(archive, "xl/workbook.xml")
        relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
        targets = {}
        for relation in relationships:
            if relation.attrib.get("TargetMode") == "External":
                continue
            target = relation.attrib.get("Target", "")
            if "\\" in target or ":" in target:
                continue
            path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
            if path.startswith("xl/worksheets/") and path.endswith(".xml"):
                targets[relation.attrib.get("Id", "")] = path
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = _xml(archive, "xl/sharedStrings.xml")
            for item in shared_root:
                if len(shared) >= 50000:
                    _error("表格的独立文字项过多，请拆分表格后上传。")
                shared.append("".join(part.text or "" for part in item.iter() if part.tag.rsplit("}", 1)[-1] == "t"))
        sheets = workbook.findall(".//{*}sheet")
        if not sheets:
            _error("XLSX 中没有可读取的工作表。")
        if len(sheets) > MAX_SHEETS:
            text.truncated = True
        for sheet in sheets[:MAX_SHEETS]:
            relation_id = next((value for key, value in sheet.attrib.items() if key.rsplit("}", 1)[-1] == "id"), "")
            target = targets.get(relation_id)
            if not target:
                _error("表格工作表引用异常，请另存为普通 XLSX 后上传。")
            root = _xml(archive, target)
            if not text.add("\n[工作表：" + sheet.attrib.get("name", "未命名") + "]\n"):
                break
            rows = root.findall(".//{*}sheetData/{*}row")
            if len(rows) > MAX_ROWS:
                text.truncated = True
            for row in rows[:MAX_ROWS]:
                cells = row.findall("{*}c")
                values = [""] * MAX_COLUMNS
                highest_column = 0
                for index, cell in enumerate(cells):
                    column = _column_number(cell.attrib.get("r", "")) or index + 1
                    if column > MAX_COLUMNS:
                        text.truncated = True
                        continue
                    value = cell.findtext("{*}v", "")
                    cell_type = cell.attrib.get("t", "")
                    if cell_type == "s":
                        try:
                            shared_index = int(value)
                            if shared_index < 0:
                                raise IndexError
                            value = shared[shared_index]
                        except (ValueError, IndexError):
                            _error("表格文字索引异常，请另存为 XLSX 后上传。")
                    elif cell_type == "inlineStr":
                        value = "".join(part.text or "" for part in cell.findall(".//{*}t"))
                    elif cell_type == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                    if cell.find("{*}f") is not None:
                        value = "[公式缓存值] " + value if value else "[公式未计算]"
                    values[column - 1] = value.replace("\t", " ").replace("\n", " ")
                    has_data = has_data or bool(value.strip())
                    highest_column = max(highest_column, column)
                if highest_column and not text.add("\t".join(values[:highest_column]) + "\n"):
                    break
            if text.size >= MAX_TEXT_CHARACTERS:
                break
    if not has_data:
        _error("表格中没有可提取的单元格文字或数值。图片请通过图片附件上传。")
    return text.result(), text.truncated


def _pdf(content: bytes) -> tuple[str, bool]:
    if not content.startswith(b"%PDF-"):
        _error("文件内容不是有效的 PDF，请检查后上传。")
    text = _Text()
    # ContextVar-backed per-parse limits: no mutable global decoder settings and
    # no external binaries. A compressed page cannot expand without a budget.
    with apply_configuration(
        maximum_declared_stream_length=2_000_000,
        array_based_stream_maximum_output_length=2_000_000,
        zlib_maximum_output_length=2_000_000,
        lzw_maximum_output_length=2_000_000,
        run_length_maximum_output_length=2_000_000,
        stream_decoding_work_maximum_length=8_000_000,
        stream_parsing_work_maximum_count=100_000,
        page_tree_maximum_entries=100,
        page_tree_maximum_page_entries=3000,
        xform_maximum_invocations_per_extraction=100,
        jbig2dec_binary=None,
    ):
        with PdfReader(io.BytesIO(content), strict=True, root_object_recovery_limit=100, xref_stream_maximum_entries=100000) as reader:
            if reader.is_encrypted:
                _error("PDF 已加密，请先移除密码后上传。")
            if len(reader.pages) > MAX_PDF_PAGES:
                _error("PDF 最多支持 30 页，请拆分后上传。")
            total_stream_bytes = 0
            has_text = False
            for index, page in enumerate(reader.pages):
                stream = page.get_contents()
                if stream is not None:
                    total_stream_bytes += len(stream.get_data())
                if total_stream_bytes > 8_000_000:
                    _error("PDF 页面内容过于复杂，请导出文字或拆分后上传。")
                page_text = page.extract_text() or ""
                if page_text.strip():
                    has_text = True
                    if not text.add(f"\n[第 {index + 1} 页]\n" + page_text + "\n"):
                        break
            if not has_text:
                _error("PDF 没有可提取的文字，可能是扫描件。请将需要识别的页面另存为图片上传，或先进行 OCR。")
    return text.result(), text.truncated


def parse_document(filename: str, content: bytes) -> dict:
    """Return a bounded document payload, or an actionable 400; no disk/network I/O."""
    name = safe_filename(filename)
    ext = PurePosixPath(name).suffix.lower()
    if ext not in DOCUMENT_EXTENSIONS:
        _error("暂不支持此文件类型。请使用 TXT、MD、CSV、JSON、PDF、DOCX 或 XLSX；旧版 DOC/XLS 请先另存。")
    if not isinstance(content, bytes) or not content:
        _error("文件为空，请选择有内容的文件。")
    if len(content) > MAX_FILE_BYTES:
        _error("单个资料文件不能超过 10 MB，请拆分后上传。")
    try:
        if ext == ".pdf":
            text, truncated = _pdf(content)
        elif ext == ".docx":
            text, truncated = _docx(content)
        elif ext == ".xlsx":
            text, truncated = _xlsx(content)
        else:
            text, truncated = _text_document(content, ext)
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError, RecursionError, ET.ParseError, zipfile.BadZipFile, PyPdfError):
        _error("文件无法安全读取，可能已损坏、加密或内容过于复杂。请另存为普通文档或导出文字后上传。")
    if not text.strip():
        _error("文件中没有可提取的文字。图片或扫描件请通过图片附件上传。")
    return {"name": name, "kind": "document", "text": text, "characters": len(text), "truncated": truncated}
