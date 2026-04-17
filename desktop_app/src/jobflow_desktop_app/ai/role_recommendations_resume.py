from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..db.repositories.candidates import CandidateRecord
from .role_recommendations_models import ResumeReadResult


def _read_text_resume(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_docx_resume(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    xml_text = xml_bytes.decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", xml_text)
    return re.sub(r"\s+", " ", text).strip()


def _read_pdf_resume(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ModuleNotFoundError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        return ""


def load_resume_excerpt_result(resume_path: str, max_chars: int | None = 6000) -> ResumeReadResult:
    raw_path = str(resume_path or "").strip()
    path = Path(raw_path)
    if not raw_path:
        return ResumeReadResult()
    if not path.exists() or not path.is_file():
        return ResumeReadResult(
            error=f"Resume file could not be found: {raw_path}",
            source_type=path.suffix.lower(),
        )

    suffix = path.suffix.lower()
    try:
        if suffix in {".md", ".txt"}:
            text = _read_text_resume(path)
        elif suffix == ".docx":
            text = _read_docx_resume(path)
        elif suffix == ".pdf":
            try:
                import pypdf  # noqa: F401
            except ModuleNotFoundError:
                return ResumeReadResult(
                    error=(
                        "Resume PDF parsing is unavailable in this build because the 'pypdf' package is not installed. "
                        "Install pypdf, or convert the resume to .docx, .md, or .txt first."
                    ),
                    source_type=suffix,
                )
            text = _read_pdf_resume(path)
        else:
            text = _read_text_resume(path)
    except Exception as exc:
        return ResumeReadResult(
            error=f"Resume file could not be read: {exc}",
            source_type=suffix,
        )

    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        if suffix == ".pdf":
            return ResumeReadResult(
                error=(
                    "Resume PDF contains no extractable text. It may be a scanned or image-only PDF. "
                    "Please OCR it, or convert it to .docx, .md, or .txt before asking for AI role recommendations."
                ),
                source_type=suffix,
            )
        return ResumeReadResult(
            error=f"Resume file is empty or unreadable: {raw_path}",
            source_type=suffix,
        )

    if max_chars is not None and max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n...[truncated]"
    return ResumeReadResult(text=text, source_type=suffix)


def load_resume_excerpt(resume_path: str, max_chars: int | None = 6000) -> str:
    return load_resume_excerpt_result(resume_path, max_chars=max_chars).text


def manual_background_summary(candidate: CandidateRecord) -> str:
    return candidate.notes.strip()


def build_missing_background_error(
    *,
    action_name: str,
    candidate: CandidateRecord,
    resume_result: ResumeReadResult,
) -> str:
    if resume_result.text or manual_background_summary(candidate):
        return ""

    field_name = "Professional Background / 专业摘要"
    if candidate.active_resume_path.strip():
        return (
            f"{action_name} needs usable candidate background information. "
            f"The resume could not be read and '{field_name}' is empty. "
            f"Please upload a readable resume, or fill in '{field_name}' with work history, domain focus, or core strengths first."
        )
    return (
        f"{action_name} needs usable candidate background information. "
        f"No readable resume was provided and '{field_name}' is empty. "
        f"Please upload a resume, or fill in '{field_name}' with work history, domain focus, or core strengths first."
    )


__all__ = [
    "ResumeReadResult",
    "build_missing_background_error",
    "load_resume_excerpt",
    "load_resume_excerpt_result",
    "manual_background_summary",
]
