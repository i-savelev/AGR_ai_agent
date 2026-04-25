"""Utilities for scanning and normalizing local knowledge-base documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile


EXCEL_EXTENSIONS = {".xls", ".xlsx", ".xlsb", ".ods"}
SUPPORTED_EXTENSIONS = {".docx", ".md", ".pdf", ".rtf", ".txt"} | EXCEL_EXTENSIONS
IGNORED_FILENAMES = {"readme.md"}
WORD_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass
class ScanResult:
    """Represents a discovered source file.

    :param path: Absolute path to the source file.
    :param relative_path: Path relative to the raw data directory.
    :param extension: Lower-cased file extension.
    :param supported: Whether the file can be ingested right now.
    """

    path: Path
    relative_path: Path
    extension: str
    supported: bool


def ensure_directories(raw_dir: Path, temp_dir: Path, knowledge_base_dir: Path) -> None:
    """Ensure required project directories exist."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    for category_name in ("normative", "examples", "general"):
        (raw_dir / category_name).mkdir(parents=True, exist_ok=True)

    temp_dir.mkdir(parents=True, exist_ok=True)
    for category_name in ("normative", "examples", "general"):
        (temp_dir / category_name).mkdir(parents=True, exist_ok=True)

    knowledge_base_dir.mkdir(parents=True, exist_ok=True)
    for category_name in ("normative", "examples", "general"):
        (knowledge_base_dir / category_name).mkdir(parents=True, exist_ok=True)


def scan_raw_files(raw_dir: Path) -> list[ScanResult]:
    """Scan the raw data directory.

    :param raw_dir: Directory with source materials.
    :returns: Discovered file metadata.
    """

    results: list[ScanResult] = []

    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file():
            continue

        relative_path = path.relative_to(raw_dir)
        if path.name.lower() in IGNORED_FILENAMES:
            continue
        if path.name.startswith("~$"):
            continue
        if relative_path.parts and relative_path.parts[0] == "temp":
            continue

        extension = path.suffix.lower()
        supported = extension in SUPPORTED_EXTENSIONS
        results.append(
            ScanResult(
                path=path,
                relative_path=relative_path,
                extension=extension,
                supported=supported,
            )
        )

    return results


def detect_document_type(relative_path: Path) -> str:
    """Infer a lightweight document type from the source path.

    :param relative_path: Path relative to the raw directory.
    :returns: Simple document type label.
    """

    lowered = relative_path.as_posix().lower()
    if "normative/" in lowered or "norms/" in lowered or "norm" in lowered:
        return "normative"
    if "examples/" in lowered or "example" in lowered or "sample" in lowered:
        return "example"
    return "general"


def get_category_name(document_type: str) -> str:
    """Map a logical document type to a directory name.

    :param document_type: Logical document type.
    :returns: Target category directory name.
    """

    if document_type == "normative":
        return "normative"
    if document_type == "example":
        return "examples"
    return "general"


def resolve_category_dir(result: ScanResult, output_dir: Path) -> Path:
    """Resolve the base output directory for a source file.

    :param result: Source file metadata.
    :param output_dir: Staging root directory.
    :returns: Category-specific output directory.
    """

    document_type = detect_document_type(result.relative_path)
    category_name = get_category_name(document_type)
    category_dir = output_dir / category_name

    if document_type != "example":
        return category_dir

    example_relative = result.relative_path.parent
    if example_relative.parts and example_relative.parts[0] == "examples":
        example_relative = Path(*example_relative.parts[1:])

    if example_relative == Path("."):
        return category_dir

    return category_dir / example_relative


def read_supported_text(path: Path) -> str:
    """Read a supported text-like source file.

    :param path: Source file path.
    :returns: Normalized file contents.
    """

    if path.suffix.lower() == ".docx":
        return read_docx_text(path)
    if path.suffix.lower() == ".pdf":
        return read_pdf_text(path)
    if path.suffix.lower() == ".rtf":
        return read_rtf_text(path)
    if path.suffix.lower() in EXCEL_EXTENSIONS:
        return read_excel_text(path)

    text = path.read_text(encoding="utf-8")
    return text.strip() + "\n"


def read_excel_sheets(path: Path) -> list[tuple[str, str]]:
    """Read an Excel workbook and convert each sheet into CSV text.

    :param path: Source Excel file path.
    :returns: Pairs of sheet name and CSV text.
    """

    try:
        import pandas as pd
    except ImportError as exc:
        raise ValueError("Pandas is required to parse Excel files.") from exc

    try:
        workbook = pd.read_excel(path, sheet_name=None, dtype=str, keep_default_na=False)
    except Exception as exc:  # pragma: no cover - pandas engine errors vary by format
        raise ValueError(f"Failed to parse Excel file: {path}") from exc

    sheets: list[tuple[str, str]] = []
    for sheet_name, frame in workbook.items():
        csv_text = frame.fillna("").to_csv(index=False)
        csv_text = csv_text.strip() + "\n"
        sheets.append((sheet_name, csv_text))

    return sheets


def read_excel_text(path: Path) -> str:
    """Read an Excel workbook and combine all sheets into text.

    :param path: Source Excel file path.
    :returns: CSV-like text for all sheets.
    """

    sheets = read_excel_sheets(path)
    blocks: list[str] = []
    for sheet_name, csv_text in sheets:
        blocks.append(f"# Sheet: {sheet_name}")
        blocks.append(csv_text.strip())
    return "\n\n".join(blocks).strip() + "\n"


def decode_rtf_unicode(match: re.Match[str]) -> str:
    """Decode an RTF unicode escape sequence.

    :param match: Regex match for ``\\uNNNN``.
    :returns: Decoded Unicode character.
    """

    value = int(match.group(1))
    if value < 0:
        value += 65536
    return chr(value)


def decode_rtf_hex(match: re.Match[str]) -> str:
    """Decode an RTF hex escape sequence using cp1251.

    :param match: Regex match for ``\\'hh``.
    :returns: Decoded character.
    """

    return bytes.fromhex(match.group(1)).decode("cp1251", errors="ignore")


def strip_rtf_destination(text: str, destination: str) -> str:
    """Remove a top-level RTF destination group by name.

    :param text: Raw RTF text.
    :param destination: Destination control word without leading backslash.
    :returns: RTF text without the destination group.
    """

    marker = "{\\" + destination
    while True:
        start = text.find(marker)
        if start == -1:
            return text

        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    text = text[:start] + text[index + 1 :]
                    break
        else:
            return text


def normalize_rtf_text_content(text: str) -> str:
    """Normalize already-decoded RTF text into plain text.

    :param text: Decoded RTF text with control words still present.
    :returns: Plain text.
    """

    text = text.replace("\\pard", "\n")
    text = text.replace("\\par", "\n")
    text = text.replace("\\line", "\n")
    text = text.replace("\\tab", "\t")
    text = text.replace("\\cell", "\n")
    text = text.replace("\\row", "\n")
    text = text.replace("\\intbl", " ")
    text = text.replace("\\*", "")
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"(?<!\S)x\d+(?!\S)", "", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_rtf_table(match: re.Match[str]) -> str:
    """Convert a simple RTF table block into Markdown.

    :param match: Regex match for one or more table rows.
    :returns: Markdown table block.
    """

    block = match.group(0)
    rows: list[list[str]] = []

    for row_match in re.finditer(r"\\trowd.*?\\row", block, flags=re.DOTALL):
        row_text = row_match.group(0)
        cells: list[str] = []
        for cell_fragment in row_text.split("\\cell")[:-1]:
            if "\\pard" in cell_fragment:
                cell_fragment = cell_fragment[cell_fragment.rfind("\\pard") :]
            cell_text = normalize_rtf_text_content(cell_fragment)
            cell_text = "\n".join(
                line
                for line in cell_text.splitlines()
                if not re.fullmatch(r"x\d+", line.strip())
            ).strip()
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(cells)

    if not rows:
        return "\n"

    max_columns = max(len(row) for row in rows)
    normalized_rows = [row + [" "] * (max_columns - len(row)) for row in rows]
    header = normalized_rows[0]
    separator = ["---"] * max_columns

    markdown_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in normalized_rows[1:]:
        markdown_lines.append("| " + " | ".join(row) + " |")

    return "\n" + "\n".join(markdown_lines) + "\n"


def format_rtf_headings(text: str) -> str:
    """Promote simple plain-text headings extracted from RTF into Markdown.

    :param text: Plain text extracted from RTF.
    :returns: Text with basic Markdown headings.
    """

    formatted_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            formatted_lines.append("")
            continue

        if re.fullmatch(r"\d+(\.\d+)* [^\n:]+", line):
            formatted_lines.append(f"## {line}")
            continue

        if re.fullmatch(r"Приложение [А-ЯA-Z]", line):
            formatted_lines.append(f"# {line}")
            continue

        if re.fullmatch(r"(Предисловие|Введение|Библиография|Сведения о своде правил)", line):
            formatted_lines.append(f"# {line}")
            continue

        if (
            len(line) <= 120
            and not line.startswith("|")
            and re.fullmatch(r"[A-ZА-ЯЁ0-9 .,:;\"()/-]+", line)
            and any(character.isalpha() for character in line)
        ):
            formatted_lines.append(f"# {line}")
            continue

        formatted_lines.append(line)

    text = "\n".join(formatted_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_rtf_text(path: Path) -> str:
    """Extract a basic plain-text representation from an RTF file.

    :param path: Source RTF file path.
    :returns: Extracted document text.
    """

    text = path.read_text(encoding="cp1251", errors="ignore")
    for destination in ("fonttbl", "colortbl", "stylesheet", "pict"):
        text = strip_rtf_destination(text, destination)

    text = re.sub(r"\{\\pict[\s\S]*?\}", "", text)
    text = re.sub(r"\\u(-?\d+)\??", decode_rtf_unicode, text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", decode_rtf_hex, text)
    text = re.sub(r"(\\trowd.*?\\row\s*)+", render_rtf_table, text, flags=re.DOTALL)
    text = normalize_rtf_text_content(text)
    text = format_rtf_headings(text)
    return text + "\n"


def read_pdf_text(path: Path) -> str:
    """Extract a minimal plain-text representation from a PDF file.

    :param path: Source PDF file path.
    :returns: Extracted document text.
    :raises ValueError: If the PDF cannot be parsed.
    """

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("pypdf is required to parse PDF files.") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # pragma: no cover - parser errors vary by file
        raise ValueError(f"Failed to parse PDF file: {path}") from exc

    blocks: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        normalized_text = page_text.strip()
        if not normalized_text:
            continue

        blocks.append(f"# Page {page_number}")
        blocks.append(normalized_text)

    if not blocks:
        return ""

    return "\n\n".join(blocks).strip() + "\n"


def extract_text_from_runs(parent: ET.Element) -> str:
    """Extract visible text from a WordprocessingML node.

    :param parent: XML node containing text runs.
    :returns: Concatenated text content.
    """

    chunks: list[str] = []
    for text_node in parent.findall(".//w:t", WORD_NAMESPACE):
        if text_node.text:
            chunks.append(text_node.text)
    return "".join(chunks).strip()


def get_paragraph_style_id(paragraph: ET.Element) -> str:
    """Return the raw style identifier for a DOCX paragraph.

    :param paragraph: Paragraph XML node.
    :returns: Style identifier or an empty string.
    """

    style = paragraph.find("./w:pPr/w:pStyle", WORD_NAMESPACE)
    if style is None:
        return ""
    return style.attrib.get(f"{{{WORD_NAMESPACE['w']}}}val", "").strip()


def get_heading_level(style_id: str) -> int | None:
    """Map a DOCX style identifier to a Markdown heading level.

    :param style_id: Raw DOCX paragraph style id.
    :returns: Heading level when the style is heading-like, else ``None``.
    """

    if not style_id:
        return None

    normalized = style_id.lower()
    if normalized.isdigit():
        level = int(normalized)
        if 1 <= level <= 6:
            return level

    match = re.search(r"heading\s*([1-6])|^([1-6])$", normalized)
    if match:
        level = next(group for group in match.groups() if group is not None)
        return int(level)

    return None


def parse_docx_paragraph(paragraph: ET.Element) -> str:
    """Parse a DOCX paragraph into plain text.

    :param paragraph: Paragraph XML node.
    :returns: Extracted paragraph text.
    """

    text = extract_text_from_runs(paragraph)
    if not text:
        return ""

    heading_level = get_heading_level(get_paragraph_style_id(paragraph))
    if heading_level is not None:
        return f"{'#' * heading_level} {text}"

    return text


def get_grid_span(cell: ET.Element) -> int:
    """Return the horizontal span of a DOCX table cell.

    :param cell: Table cell XML node.
    :returns: Grid span value or ``1`` when absent.
    """

    grid_span = cell.find("./w:tcPr/w:gridSpan", WORD_NAMESPACE)
    if grid_span is None:
        return 1

    raw_value = grid_span.attrib.get(f"{{{WORD_NAMESPACE['w']}}}val", "1").strip()
    if not raw_value.isdigit():
        return 1
    return max(int(raw_value), 1)


def expand_table_row(row: ET.Element) -> list[str]:
    """Expand a DOCX table row with respect to horizontal merged cells.

    :param row: Table row XML node.
    :returns: Row cells aligned to the table grid.
    """

    expanded_cells: list[str] = []
    for cell in row.findall("./w:tc", WORD_NAMESPACE):
        paragraphs = cell.findall("./w:p", WORD_NAMESPACE)
        cell_text = "\n".join(
            text for text in (parse_docx_paragraph(item) for item in paragraphs) if text
        ).strip()
        normalized_cell = (cell_text or " ").replace("\n", "<br>")
        span = get_grid_span(cell)
        expanded_cells.append(normalized_cell)
        expanded_cells.extend([" "] * (span - 1))

    return expanded_cells


def get_table_header_row_count(rows: list[list[str]]) -> int:
    """Infer how many initial rows should be treated as a table header.

    :param rows: Normalized table rows.
    :returns: Header row count.
    """

    if not rows:
        return 0

    first_row = rows[0]
    non_empty_first_row = [cell.strip() for cell in first_row if cell.strip()]
    if not non_empty_first_row:
        return 0

    if len(rows) == 1:
        return 1

    second_row = rows[1]
    second_row_values = [cell.strip() for cell in second_row if cell.strip()]
    if second_row_values and all(value.isdigit() for value in second_row_values):
        return 2

    return 1


def parse_docx_table(table: ET.Element) -> str:
    """Parse a DOCX table into a simple Markdown table.

    :param table: Table XML node.
    :returns: Markdown representation of the table.
    """

    rows: list[list[str]] = []
    for row in table.findall("./w:tr", WORD_NAMESPACE):
        expanded_row = expand_table_row(row)
        if any(cell.strip() for cell in expanded_row):
            rows.append(expanded_row)

    if not rows:
        return ""

    max_columns = max(len(row) for row in rows)
    normalized_rows = [row + [" "] * (max_columns - len(row)) for row in rows]
    header_row_count = get_table_header_row_count(normalized_rows)
    if header_row_count == 0:
        return ""

    header_candidates = normalized_rows[:header_row_count]
    header = []
    for column_index in range(max_columns):
        parts: list[str] = []
        for row in header_candidates:
            value = row[column_index].strip()
            if not value or value.isdigit():
                continue
            if value not in parts:
                parts.append(value)
        header.append(" / ".join(parts) if parts else f"Column {column_index + 1}")

    separator = ["---"] * max_columns

    markdown_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in normalized_rows[header_row_count:]:
        markdown_lines.append("| " + " | ".join(row) + " |")

    return "\n".join(markdown_lines)


def read_docx_text(path: Path) -> str:
    """Extract a minimal Markdown-like text representation from a DOCX file.

    :param path: Source DOCX file path.
    :returns: Extracted document text with simple table support.
    """

    try:
        with ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
    except (FileNotFoundError, KeyError, BadZipFile) as exc:
        raise ValueError(f"Failed to parse DOCX file: {path}") from exc

    root = ET.fromstring(document_xml)
    body = root.find("./w:body", WORD_NAMESPACE)
    if body is None:
        return ""

    blocks: list[str] = []
    for child in body:
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name == "p":
            paragraph_text = parse_docx_paragraph(child)
            if paragraph_text:
                blocks.append(paragraph_text)
        elif local_name == "tbl":
            table_text = parse_docx_table(child)
            if table_text:
                blocks.append(table_text)

    return "\n\n".join(blocks).strip() + "\n"


def render_markdown_document(result: ScanResult, content: str) -> str:
    """Render the canonical knowledge-base markdown document.

    :param result: Source file metadata.
    :param content: Source text content.
    :returns: Markdown document with metadata.
    """

    title = result.relative_path.stem.replace("_", " ").replace("-", " ").strip()
    title = title or "Untitled Document"
    ingested_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    document_type = detect_document_type(result.relative_path)

    metadata = [
        "---",
        f'title: "{title}"',
        f"source_file: {result.path.name}",
        f"source_relpath: {result.relative_path.as_posix()}",
        f"document_type: {document_type}",
        f"ingested_at: {ingested_at}",
        "status: active",
        "---",
        "",
    ]

    return "\n".join(metadata) + content


def get_output_stem(relative_path: Path) -> str:
    """Return the output filename stem derived from the source filename.

    :param relative_path: Path relative to the raw directory.
    :returns: Source filename without the last extension.
    """

    stem = relative_path.stem.strip()
    return stem or "document"


def build_legacy_slug_stem(relative_path: Path) -> str:
    """Return the previous slug-style stem used by older ingestion runs.

    :param relative_path: Path relative to the raw directory.
    :returns: Legacy ASCII-only file stem.
    """

    parts = list(relative_path.with_suffix("").parts)
    if parts and parts[0].lower() in {"normative", "examples", "general"}:
        parts = parts[1:]

    normalized = "/".join(parts).lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "document"


def resolve_unique_output_path(
    category_dir: Path,
    preferred_stem: str,
    extension: str,
) -> Path:
    """Resolve a unique markdown path inside a category directory.

    :param category_dir: Target knowledge-base category directory.
    :param preferred_stem: Preferred filename stem.
    :returns: Unique target markdown path.
    """

    candidate = category_dir / f"{preferred_stem}{extension}"
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        candidate = category_dir / f"{preferred_stem}-{suffix}{extension}"
        if not candidate.exists():
            return candidate
        suffix += 1


def resolve_output_path(result: ScanResult, output_dir: Path) -> Path:
    """Resolve the target path for an ingested document.

    :param result: Source file metadata.
    :param output_dir: Knowledge-base root directory.
    :returns: Output markdown file path.
    """

    category_dir = resolve_category_dir(result, output_dir)
    category_dir.mkdir(parents=True, exist_ok=True)

    preferred_path = category_dir / f"{get_output_stem(result.relative_path)}.md"
    if not preferred_path.exists():
        return preferred_path

    existing_text = preferred_path.read_text(encoding="utf-8")
    if f"source_relpath: {result.relative_path.as_posix()}" in existing_text:
        return preferred_path

    return resolve_unique_output_path(category_dir, get_output_stem(result.relative_path), ".md")


def resolve_excel_output_path(result: ScanResult, output_dir: Path, sheet_name: str) -> Path:
    """Resolve the target CSV path for one Excel sheet.

    :param result: Source file metadata.
    :param output_dir: Target staging root directory.
    :param sheet_name: Excel sheet name.
    :returns: Output CSV file path.
    """

    category_dir = resolve_category_dir(result, output_dir)
    category_dir.mkdir(parents=True, exist_ok=True)

    preferred_stem = f"{get_output_stem(result.relative_path)}__{get_safe_filename_component(sheet_name)}"
    return category_dir / f"{preferred_stem}.csv"


def get_safe_filename_component(value: str) -> str:
    """Return a filesystem-safe filename component.

    :param value: Raw value to normalize.
    :returns: Safe filename fragment.
    """

    normalized = re.sub(r"\s+", "_", value.strip())
    normalized = re.sub(r"[^\w.-]+", "_", normalized, flags=re.UNICODE)
    return normalized.strip("._-") or "sheet"


def remove_legacy_output_file(result: ScanResult, target_path: Path) -> None:
    """Remove the legacy slug-named file for the same source when safe.

    :param result: Source file metadata.
    :param target_path: Current resolved output path.
    """

    legacy_path = target_path.parent / f"{build_legacy_slug_stem(result.relative_path)}.md"
    if legacy_path == target_path or not legacy_path.exists():
        return

    legacy_text = legacy_path.read_text(encoding="utf-8")
    if f"source_relpath: {result.relative_path.as_posix()}" not in legacy_text:
        return

    legacy_path.unlink()


def ingest_supported_files(
    scan_results: list[ScanResult],
    temp_dir: Path,
    force: bool = False,
) -> dict[str, object]:
    """Ingest supported source files into the temp staging area.

    :param scan_results: Previously discovered scan results.
    :param temp_dir: Target staging directory for normalized markdown files.
    :param force: Rewrite existing output files when set.
    :returns: Ingestion summary.
    """

    temp_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped_existing: list[str] = []
    unsupported: list[str] = []

    for result in scan_results:
        if not result.supported:
            unsupported.append(result.relative_path.as_posix())
            continue

        if result.extension in EXCEL_EXTENSIONS:
            for sheet_name, csv_text in read_excel_sheets(result.path):
                target_path = resolve_excel_output_path(result, temp_dir, sheet_name)
                if target_path.exists() and not force:
                    skipped_existing.append(target_path.relative_to(temp_dir).as_posix())
                    continue

                target_path.write_text(csv_text, encoding="utf-8")
                written.append(target_path.relative_to(temp_dir).as_posix())
            continue

        target_path = resolve_output_path(result, output_dir=temp_dir)
        if target_path.exists() and not force:
            skipped_existing.append(target_path.relative_to(temp_dir).as_posix())
            continue

        content = read_supported_text(result.path)
        markdown_document = render_markdown_document(result, content)
        target_path.write_text(markdown_document, encoding="utf-8")
        if force:
            remove_legacy_output_file(result, target_path)
        written.append(target_path.relative_to(temp_dir).as_posix())

    return {
        "output_dir": str(temp_dir),
        "written": written,
        "skipped_existing": skipped_existing,
        "unsupported": unsupported,
    }
