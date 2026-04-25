"""Utilities for exporting Markdown documents to Microsoft Word."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


def resolve_pandoc_executable(pandoc_path: str | Path | None = None) -> str:
    """Resolve the pandoc executable path.

    :param pandoc_path: Optional explicit path to the pandoc executable.
    :returns: Resolved executable path.
    :raises RuntimeError: If pandoc is not available.
    """

    if pandoc_path is not None:
        candidate = Path(pandoc_path)
        if candidate.exists():
            return str(candidate)
        raise RuntimeError(f"Pandoc executable not found: {candidate}")

    resolved = shutil.which("pandoc")
    if resolved:
        return resolved

    try:
        import pypandoc
    except ImportError:
        pypandoc = None

    if pypandoc is not None:
        try:
            resolved = pypandoc.get_pandoc_path()
        except OSError:
            resolved = None
        if resolved:
            return str(resolved)

    raise RuntimeError(
        "Pandoc executable is not available. Install pandoc or provide pandoc_path explicitly."
    )


def convert_markdown_to_docx(
    source_path: str | Path,
    target_path: str | Path,
    reference_docx: str | Path | None = None,
    pandoc_path: str | Path | None = None,
) -> Path:
    """Convert a Markdown file into a Word document using pandoc.

    :param source_path: Path to the source Markdown file.
    :param target_path: Path to the target ``.docx`` file.
    :param reference_docx: Optional reference ``.docx`` with styles.
    :param pandoc_path: Optional explicit path to the pandoc executable.
    :returns: Path to the written ``.docx`` file.
    :raises FileNotFoundError: If the source Markdown file does not exist.
    :raises RuntimeError: If pandoc is missing or conversion fails.
    """

    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Markdown source file not found: {source}")

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    command = [
        resolve_pandoc_executable(pandoc_path=pandoc_path),
        str(source),
        "--from",
        "markdown",
        "--to",
        "docx",
        "--output",
        str(target),
    ]

    if reference_docx is not None:
        reference = Path(reference_docx)
        if not reference.is_file():
            raise FileNotFoundError(f"Reference docx file not found: {reference}")
        command.extend(["--reference-doc", str(reference)])

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "Unknown pandoc error."
        raise RuntimeError(f"Pandoc conversion failed: {stderr}")

    return target
