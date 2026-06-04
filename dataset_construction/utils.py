#!/usr/bin/env python3
"""Shared helpers for Appendix 5 QA generation scripts."""

from __future__ import annotations

import os
import re
from pathlib import Path

from openpyxl import load_workbook

SCRIPT_DIR = Path(__file__).parent
MODEL_ID = "gemini-3-flash-preview"
SYSTEM_PROMPT = "You are a helpful assistant creating FAQ datasets for clinical trials."
GENERATION_CONFIG_DEFAULTS = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 64,
    "candidate_count": 1,
}


def get_client():
    """Initialize the Gemini client using local key file or env var."""
    api_key_path = SCRIPT_DIR / "gemini_key.txt"
    # Alternative: set GEMINI_API_KEY environment variable
    if api_key_path.exists():
        api_key = api_key_path.read_text(encoding="utf-8").strip()
    else:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("Please set GEMINI_API_KEY or provide gemini_key.txt.")

    from google import genai

    os.environ["GEMINI_API_KEY"] = api_key
    return genai.Client(api_key=api_key)


def build_generation_config(response_mime_type: str):
    from google.genai import types

    return types.GenerateContentConfig(
        response_mime_type=response_mime_type,
        system_instruction=[types.Part.from_text(text=SYSTEM_PROMPT)],
        temperature=GENERATION_CONFIG_DEFAULTS["temperature"],
        top_p=GENERATION_CONFIG_DEFAULTS["top_p"],
        top_k=GENERATION_CONFIG_DEFAULTS["top_k"],
        candidate_count=GENERATION_CONFIG_DEFAULTS["candidate_count"],
    )


def load_icf_markdown(cancer_type: str, trial_id: str) -> tuple[Path, str]:
    """Load ICF markdown from the expected folder layout."""
    path = SCRIPT_DIR / "ICF_text" / cancer_type / f"{trial_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"ICF markdown not found: {path}")
    return path, path.read_text(encoding="utf-8")


def load_element_definitions() -> dict[str, str]:
    """Load only element/definition columns from ICF_element.xlsx."""
    workbook_path = SCRIPT_DIR / "ICF_element.xlsx"
    if not workbook_path.exists():
        raise FileNotFoundError(f"Element workbook not found: {workbook_path}")

    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    headers = [cell for cell in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    try:
        element_idx = headers.index("element")
        definition_idx = headers.index("definition")
    except ValueError as exc:
        raise ValueError(f"Workbook must contain 'element' and 'definition' columns: {headers}") from exc

    definitions: dict[str, str] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        element = row[element_idx]
        definition = row[definition_idx]
        if not element:
            continue
        definitions[str(element)] = "" if definition is None else str(definition)
    return definitions


def split_markdown_into_evidence_chunks(markdown_text: str, max_chars: int = 2000) -> list[dict[str, str | int]]:
    """Create numbered evidence chunks using headings as navigation helpers."""
    text = markdown_text.strip()
    if not text:
        return []

    lines = text.splitlines()
    sections: list[dict[str, str]] = []
    current_heading = "Document Start"
    current_lines: list[str] = []

    def flush_section():
        body = "\n".join(current_lines).strip()
        if body:
            sections.append({"heading": current_heading, "text": f"{current_heading}\n{body}".strip()})

    heading_pattern = re.compile(r"^\s{0,3}#{1,6}\s+.+$")

    for line in lines:
        if heading_pattern.match(line):
            flush_section()
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush_section()

    # Fall back to paragraph chunks if headings are sparse or absent.
    if len(sections) <= 1:
        paragraphs = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
        sections = []
        for idx, paragraph in enumerate(paragraphs, start=1):
            sections.append({"heading": f"Paragraph Block {idx}", "text": f"Paragraph Block {idx}\n{paragraph}"})

    chunks: list[dict[str, str | int]] = []
    for section in sections:
        section_text = section["text"]
        heading = section["heading"]
        if len(section_text) <= max_chars:
            chunks.append({"id": len(chunks) + 1, "heading": heading, "snippet": section_text})
            continue

        paragraphs = [block.strip() for block in re.split(r"\n\s*\n", section_text) if block.strip()]
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append({"id": len(chunks) + 1, "heading": heading, "snippet": current})
            current = paragraph[:max_chars]
        if current:
            chunks.append({"id": len(chunks) + 1, "heading": heading, "snippet": current})

    return chunks
