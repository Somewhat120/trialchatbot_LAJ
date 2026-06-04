#!/usr/bin/env python3
"""Standalone answer generation for clinician-approved Appendix 5 questions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from utils import (
    MODEL_ID,
    SYSTEM_PROMPT,
    build_generation_config,
    get_client,
    load_icf_markdown,
    split_markdown_into_evidence_chunks,
)


def load_approved_questions(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Approved questions JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Approved questions JSON must be a flat array of objects.")
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Question #{idx} is not an object.")
        if not item.get("element") or not item.get("question"):
            raise ValueError(f"Question #{idx} must include 'element' and 'question'.")
    return data


def build_evidence_block(chunks: list[dict]) -> str:
    parts = []
    for chunk in chunks:
        parts.append(
            f"[{chunk['id']}] {chunk['heading']}\n{chunk['snippet']}"
        )
    return "\n\n".join(parts)


def build_answer_prompt(trial_id: str, cancer_type: str, question_item: dict, evidence_block: str) -> str:
    question_type = question_item.get("question_type", "UNKNOWN")
    element = question_item["element"]
    question = question_item["question"]

    return f"""CORE TASK:
Answer one clinician-approved participant question for a clinical trial using the full ICF evidence below.

TRIAL_ID: {trial_id}
CANCER_TYPE: {cancer_type}
ELEMENT_NAME: {element}
QUESTION_TYPE: {question_type}
QUESTION: {question}

EVIDENCE USE INSTRUCTIONS:
- The numbered evidence chunks are navigation helpers only.
- Section headings may vary across ICFs or use different wording.
- Search across the whole ICF for semantically equivalent content rather than relying on exact heading matches.
- If the answer requires trial-specific detail that the ICF does not provide, say: "The ICF does not specify..."

answer_good:
- Must be fully supported by the provided ICF evidence.
- Do NOT introduce trial-specific facts that are not supported by the ICF.
- The trial-specific part must remain grounded in the ICF.
- General background can be used only for a mixed question's general concept component, not to invent trial-specific facts.

answer_drifted:
- Write a plausible but lower-quality answer.
- Drift may affect clinical responsibility, factual accuracy, completeness, safety/ethics, relevancy, verbosity, or clarity.
- Drift should be realistic and subtle.
- Drifted answers do NOT need to be grounded.

evidence_ids:
- Include a list of numbered evidence chunk IDs that support answer_good.
- Only include IDs for chunks that actually support claims made in answer_good.
- Format: [1, 3, 5]

OUTPUT RULES:
- Output JSON ONLY as a JSON object.
- Do not include commentary outside the JSON object.

REQUIRED OUTPUT FORMAT:
{{
  "element": "{element}",
  "question": "{question}",
  "answer_good": "...",
  "answer_drifted": "...",
  "evidence_ids": [1, 2]
}}

ICF_EVIDENCE:
<<<
{evidence_block}
>>>"""


def run_generation(prompt_text: str):
    client = get_client()
    from google.genai import types

    response = client.models.generate_content(
        model=MODEL_ID,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt_text)])],
        config=build_generation_config(response_mime_type="application/json"),
    )
    return json.loads(response.text)


def attach_evidence_snippets(item: dict, chunks: list[dict]) -> dict:
    id_to_snippet = {chunk["id"]: chunk["snippet"] for chunk in chunks}
    evidence_ids = item.get("evidence_ids", [])
    item["evidence_snippets"] = [id_to_snippet[evidence_id] for evidence_id in evidence_ids if evidence_id in id_to_snippet]
    return item


def parse_args():
    parser = argparse.ArgumentParser(description="Generate answers for clinician-approved trial questions.")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--cancer-type", required=True)
    parser.add_argument("--questions-json", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    questions = load_approved_questions(args.questions_json)
    icf_path, icf_text = load_icf_markdown(args.cancer_type, args.trial_id)
    chunks = split_markdown_into_evidence_chunks(icf_text)
    evidence_block = build_evidence_block(chunks)
    output_path = args.output or Path(__file__).parent / f"{args.trial_id}_qa_pairs.json"

    if args.dry_run:
        first_prompt = build_answer_prompt(args.trial_id, args.cancer_type, questions[0], evidence_block)
        print(f"SYSTEM PROMPT:\n{SYSTEM_PROMPT}\n")
        print(f"ICF PATH: {icf_path}")
        print(f"QUESTIONS JSON: {args.questions_json}")
        print(f"OUTPUT PATH: {output_path}")
        print("\nNUMBERED ICF EVIDENCE:\n")
        print(evidence_block)
        print("\nREPRESENTATIVE USER PROMPT:\n")
        print(first_prompt)
        return

    results = []
    for question_item in questions:
        prompt_text = build_answer_prompt(args.trial_id, args.cancer_type, question_item, evidence_block)
        response_item = run_generation(prompt_text)
        if "question_type" in question_item and "question_type" not in response_item:
            response_item["question_type"] = question_item["question_type"]
        results.append(attach_evidence_snippets(response_item, chunks))

    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote QA pairs: {output_path}")


if __name__ == "__main__":
    main()
