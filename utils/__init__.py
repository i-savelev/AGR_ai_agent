"""Public Python API for the BIM standart utils project."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "ScanResult",
    "ensure_directories",
    "ingest_sources",
    "read_docx_text",
    "read_pdf_text",
    "read_rtf_text",
    "read_supported_text",
    "scan_sources",
    "convert_markdown_to_docx",
]


def __getattr__(name: str) -> Any:
    """Resolve public package attributes lazily.

    :param name: Requested public attribute name.
    :returns: Exported object from the corresponding module.
    :raises AttributeError: If the attribute is not part of the public API.
    """

    if name == "convert_markdown_to_docx":
        module = import_module(".word_export", __name__)
        return module.convert_markdown_to_docx

    ingestion_exports = {
        "ScanResult": "ScanResult",
        "ensure_directories": "ensure_directories",
        "ingest_sources": "ingest_supported_files",
        "read_docx_text": "read_docx_text",
        "read_pdf_text": "read_pdf_text",
        "read_rtf_text": "read_rtf_text",
        "read_supported_text": "read_supported_text",
        "scan_sources": "scan_raw_files",
    }
    if name in ingestion_exports:
        module = import_module(".ingestion", __name__)
        return getattr(module, ingestion_exports[name])

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
