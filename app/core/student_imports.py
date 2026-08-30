import csv
import io
import json
import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from xml.etree import ElementTree

from fastapi import HTTPException, UploadFile, status


MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
IMPORT_TTL_SECONDS = 24 * 60 * 60
IMPORT_DIR = Path(tempfile.gettempdir()) / "campusid_student_imports"
_CELL_REFERENCE = re.compile(r"([A-Z]+)")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _validate_table(headers: list[Any], rows: list[list[Any]]) -> tuple[list[str], list[dict[str, str]]]:
    normalized_headers = [_cell_text(value) for value in headers]
    if not normalized_headers or not any(normalized_headers):
        raise HTTPException(status_code=422, detail="The spreadsheet has no header row")
    if any(not value for value in normalized_headers):
        raise HTTPException(status_code=422, detail="Every spreadsheet column must have a header")
    if len(normalized_headers) != len(set(normalized_headers)):
        raise HTTPException(status_code=422, detail="Spreadsheet headers must be unique")
    mapped_rows = []
    for values in rows:
        row = {
            header: _cell_text(values[index]) if index < len(values) else ""
            for index, header in enumerate(normalized_headers)
        }
        if any(row.values()):
            mapped_rows.append(row)
    if not mapped_rows:
        raise HTTPException(status_code=422, detail="The spreadsheet has no student rows")
    if len(mapped_rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status_code=422, detail=f"Imports are limited to {MAX_IMPORT_ROWS} rows")
    return normalized_headers, mapped_rows


def _column_index(reference: str) -> int:
    match = _CELL_REFERENCE.match(reference)
    if match is None:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - 64
    return value - 1


def _read_xlsx(content: bytes) -> tuple[list[Any], list[list[Any]]]:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 25 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Expanded XLSX data exceeds the 25 MB limit")
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.iter(f"{namespace}t")) for item in root]
            date_styles: set[int] = set()
            if "xl/styles.xml" in archive.namelist():
                styles = ElementTree.fromstring(archive.read("xl/styles.xml"))
                custom_formats = {
                    int(item.attrib["numFmtId"]): item.attrib.get("formatCode", "")
                    for item in styles.findall(f"{namespace}numFmts/{namespace}numFmt")
                }
                date_format_ids = set(range(14, 23)) | {45, 46, 47}
                date_format_ids.update(
                    format_id
                    for format_id, code in custom_formats.items()
                    if re.search(r"[ymd]", re.sub(r'"[^\"]*"|\\.', "", code), re.IGNORECASE)
                )
                cell_formats = styles.find(f"{namespace}cellXfs")
                if cell_formats is not None:
                    date_styles = {
                        index
                        for index, item in enumerate(cell_formats)
                        if int(item.attrib.get("numFmtId", "0")) in date_format_ids
                    }
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            workbook_properties = workbook.find(f"{namespace}workbookPr")
            uses_1904_dates = (
                workbook_properties is not None
                and workbook_properties.attrib.get("date1904", "false").lower()
                in {"1", "true"}
            )
            excel_date_base = (
                datetime(1904, 1, 1)
                if uses_1904_dates
                else datetime(1899, 12, 30)
            )
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            first_sheet = workbook.find(f"{namespace}sheets/{namespace}sheet")
            relation_id = first_sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = next(item.attrib["Target"] for item in relationships if item.attrib["Id"] == relation_id)
            target = target.lstrip("/")
            sheet_path = target if target.startswith("xl/") else f"xl/{target}"
            sheet = ElementTree.fromstring(archive.read(sheet_path))
            table = []
            for row_node in sheet.iter(f"{namespace}row"):
                values: list[str] = []
                for cell in row_node.findall(f"{namespace}c"):
                    index = _column_index(cell.attrib.get("r", "A1"))
                    while len(values) <= index:
                        values.append("")
                    value_node = cell.find(f"{namespace}v")
                    inline = cell.find(f"{namespace}is")
                    value = value_node.text if value_node is not None else ""
                    if cell.attrib.get("t") == "s" and value:
                        value = shared[int(value)]
                    elif inline is not None:
                        value = "".join(node.text or "" for node in inline.iter(f"{namespace}t"))
                    elif value and int(cell.attrib.get("s", "0")) in date_styles:
                        value = (
                            excel_date_base + timedelta(days=float(value))
                        ).date().isoformat()
                    values[index] = value
                table.append(values)
    except (KeyError, IndexError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise HTTPException(status_code=422, detail="The XLSX file could not be read") from exc
    if not table:
        raise HTTPException(status_code=422, detail="The XLSX file is empty")
    return table[0], table[1:]


async def parse_student_upload(file: UploadFile) -> tuple[list[str], list[dict[str, str]]]:
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file exceeds the 5 MB limit")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="CSV files must use UTF-8 encoding") from exc
        table = list(csv.reader(io.StringIO(text)))
        if not table:
            raise HTTPException(status_code=422, detail="The CSV file is empty")
        return _validate_table(table[0], table[1:])
    if suffix == ".xlsx":
        headers, rows = _read_xlsx(content)
        return _validate_table(headers, rows)
    raise HTTPException(status_code=422, detail="Only CSV and XLSX files are supported")


def save_import_manifest(*, school_uuid: UUID, user_id: int, filename: str, headers: list[str], rows: list[dict[str, str]]) -> UUID:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = uuid4()
    manifest = {"upload_id": str(upload_id), "school_uuid": str(school_uuid), "user_id": user_id, "filename": filename, "created_at": datetime.now(timezone.utc).timestamp(), "headers": headers, "rows": rows}
    (IMPORT_DIR / f"{upload_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
    return upload_id


def load_import_manifest(*, upload_id: UUID, school_uuid: UUID, user_id: int) -> dict[str, Any]:
    path = IMPORT_DIR / f"{upload_id}.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="Import upload not found or expired") from exc
    age = datetime.now(timezone.utc).timestamp() - float(manifest.get("created_at", 0))
    if age > IMPORT_TTL_SECONDS:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="Import upload not found or expired")
    if manifest.get("school_uuid") != str(school_uuid) or manifest.get("user_id") != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This import belongs to another user or school")
    return manifest


def delete_import_manifest(upload_id: UUID) -> None:
    (IMPORT_DIR / f"{upload_id}.json").unlink(missing_ok=True)
