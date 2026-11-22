from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZipFile

import pandas as pd
import pymupdf
from docx import Document
from docx.table import Table

from fileagent.filesystem import FileError, Workspace

MAX_EXTRACTED_CHARS = 240_000
MAX_PDF_PAGES = 120
MAX_CSV_ROWS = 1_000
MAX_DOCX_EXPANDED_BYTES = 40 * 1024 * 1024


@dataclass
class ExtractedText:
    text: str
    notices: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.notices)


def pdf_text(data: bytes) -> ExtractedText:
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            raise FileError("encrypted_pdf", "PDF 需要密码，无法提取。")
        pages = []
        empty_pages = 0
        remaining = MAX_EXTRACTED_CHARS
        total_pages = min(len(document), MAX_PDF_PAGES)
        for index in range(total_pages):
            text = document[index].get_text(sort=True).strip()
            if text:
                page = f"[第 {index + 1} 页]\n{text}\n\n"
                pages.append(page[:remaining])
                remaining -= len(page)
                if remaining <= 0:
                    break
            else:
                empty_pages += 1
        if not pages:
            raise FileError("no_text", "PDF 中没有可提取的文字。当前版本不含 OCR。")
        notices = []
        if len(document) > MAX_PDF_PAGES:
            notices.append("仅处理 PDF 前 120 页。")
        if empty_pages:
            notices.append(f"已检查的 PDF 中有 {empty_pages} 页无可提取文字，可能是空白页或扫描页。")
        if remaining <= 0:
            notices.append("文字提取达到 240,000 字符上限。")
        return ExtractedText("".join(pages), notices)


def docx_text(data: bytes) -> str:
    with ZipFile(BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 2_048 or sum(item.file_size for item in members) > MAX_DOCX_EXPANDED_BYTES:
            raise FileError("docx_size", "DOCX 解压后的大小或条目数超过上限。")

    document = Document(BytesIO(data))
    lines = []
    remaining = MAX_EXTRACTED_CHARS + 1
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            block_lines = (" | ".join(cell.text for cell in row.cells) for row in block.rows)
        else:
            block_lines = (block.text,)
        for line in block_lines:
            if line.strip():
                line += "\n"
                lines.append(line[:remaining])
                remaining -= len(line)
                if remaining <= 0:
                    return "".join(lines)
    return "".join(lines)


def csv_text(data: bytes) -> ExtractedText:
    try:
        frame = pd.read_csv(
            BytesIO(data), nrows=MAX_CSV_ROWS + 1, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
    except pd.errors.EmptyDataError:
        return ExtractedText("")
    notices = ["仅处理 CSV 前 1,000 条数据记录。"] if len(frame) > MAX_CSV_ROWS else []
    return ExtractedText(frame.head(MAX_CSV_ROWS).to_csv(index=False, lineterminator="\n"), notices)


def extract(workspace: Workspace, path: str) -> ExtractedText:
    data = workspace.read(path)
    suffix = PurePosixPath(path).suffix.lower()
    try:
        if suffix == ".pdf":
            result = pdf_text(data)
        elif suffix == ".docx":
            result = ExtractedText(docx_text(data))
        elif suffix == ".csv":
            result = csv_text(data)
        else:
            result = ExtractedText(data.decode("utf-8-sig"))
    except FileError:
        raise
    except UnicodeError:
        raise FileError("encoding", "文本或 CSV 须使用 UTF-8 编码，可包含 BOM。") from None
    except Exception as error:
        # The format libraries raise different exceptions for corrupt input.
        if isinstance(error, MemoryError):
            raise
        raise FileError("parse_error", "文件无法解析，可能已损坏或格式与扩展名不一致。") from None
    if not result.text.strip():
        raise FileError("no_text", "文件没有可提取的文字。")
    if len(result.text) > MAX_EXTRACTED_CHARS:
        result.text = result.text[:MAX_EXTRACTED_CHARS]
        result.notices.append("文字提取达到 240,000 字符上限。")
    return result
