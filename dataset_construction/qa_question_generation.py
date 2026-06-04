#!/usr/bin/env python3
"""Standalone question generation for Appendix 5 QA workflow."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from utils import (
    MODEL_ID,
    SYSTEM_PROMPT,
    build_generation_config,
    get_client,
    load_element_definitions,
    load_icf_markdown,
)

QUESTIONS_PER_TRIAL = 8
ELEMENT_WEIGHTS = {
    "Procedures": 18,
    "Risks or Discomforts": 12,
    "Eligibility": 8,
    "Purpose of the Research": 5,
    "Benefits": 5,
    "Alternatives": 5,
    "Compensation for Participation and Time": 5,
    "Compensation & Treatment for Injury": 4,
    "Voluntariness & Withdrawal": 10,
    "Confidentiality": 2,
    "Unknown Risks": 1,
    "Use of Data/Samples in Future Research": 2,
    "Contact Information": 1,
    "Significant New Findings": 1,
    "Termination Criteria": 1,
    "Costs to Subject": 1,
}

ELEMENT_SCOPE_MAP = {
    "Purpose of the Research": "why the study is being done, what it hopes to learn, research goals, expected study duration",
    "Procedures": "drugs, surgery, device, location, duration, visit frequency, expected steps, order of activities, tests, blood draws, follow-up visits",
    "Risks or Discomforts": "side effects, discomforts, foreseeable harms, burdens, inconvenience, pregnancy risks, procedure-related risks",
    "Benefits": "possible direct benefits, lack of direct benefit, benefits to future patients, research value",
    "Alternatives": "other treatment options, standard care, choosing not to participate, other available approaches",
    "Confidentiality": "privacy, who sees records, data sharing, de-identification, limits of confidentiality",
    "Compensation & Treatment for Injury": "what happens if injury occurs, medical care for injury, compensation availability, where to seek help",
    "Voluntariness & Withdrawal": "voluntary participation, stopping later, penalties, effect on care, withdrawal process",
    "Contact Information": "who to contact with questions, study team, IRB, emergencies, phone numbers, support channels",
    "Eligibility": "who can join, who cannot join, prior treatments, age, health status, timing rules, screening requirements",
    "Unknown Risks": "unforeseen side effects, unknown harms, unexpected problems, uncertainty in study treatment",
    "Termination Criteria": "reasons the study doctor may stop participation, rule violations, health changes, study stopping early",
    "Significant New Findings": "new information during the study, when participants would be told, how new risk or benefit information is shared",
    "Costs to Subject": "out-of-pocket costs, billed care, insurance, study-paid items, participant financial responsibility",
    "Use of Data/Samples in Future Research": "future use of tissue or data, storage, secondary research, consent choices for later use",
    "Compensation for Participation and Time": "payment, reimbursement, travel support, stipends, compensation for time and effort",
}


def sample_elements(definitions: dict[str, str], total_questions: int, rng: random.Random) -> Counter:
    available = list(definitions)
    weights = [ELEMENT_WEIGHTS.get(element, 1) for element in available]
    selected = rng.choices(available, weights=weights, k=total_questions)
    return Counter(selected)


def build_question_prompt(
    trial_id: str,
    cancer_type: str,
    icf_text: str,
    definitions: dict[str, str],
    element: str,
    count: int,
) -> str:
    definition = definitions[element]
    scope = ELEMENT_SCOPE_MAP[element]

    return f"""CORE TASK:
Generate exactly {count} candidate participant questions for trial {trial_id} in {cancer_type} cancer.

TRIAL_ID: {trial_id}
CANCER_TYPE: {cancer_type}
ELEMENT_NAME: {element}
ELEMENT_DEFINITION: {definition}
ELEMENT_SCOPE: {scope}
N_QUESTIONS: {count}

QUESTION TYPES:
1. TRIAL_SPECIFIC
- Plain 8th-grade language.
- One topic per question.
- Must be answerable from the ICF.
- Must stay within the assigned element.

2. CASE_BASED
- Write patient-like situational questions with a simple persona.
- Limit each scenario to at most 30 words and at most 2 conditions.
- Keep the question grounded in the assigned element and answerable from the ICF.

GENERAL CONSTRAINTS:
- Each question must reflect something a potential participant might realistically ask.
- Assign each question to exactly one element.
- Do not mix multiple consent elements in the same question.
- Cover different aspects of the assigned element.
- Use representative patient wording from the general population.
- The whole ICF is the source of truth, even when section headings are inconsistent.
- Output JSON ONLY as a JSON array.

REQUIRED OUTPUT FORMAT:
[
  {{
    "element": "Procedures",
    "question": "How often would I need to come in for study visits?",
    "question_type": "TRIAL_SPECIFIC"
  }}
]

FULL_ICF_MARKDOWN:
<<<
{icf_text}
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


def parse_args():
    parser = argparse.ArgumentParser(description="Generate candidate trial questions from ICF markdown.")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--cancer-type", required=True)
    parser.add_argument("--questions-per-trial", type=int, default=QUESTIONS_PER_TRIAL)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    definitions = load_element_definitions()
    icf_path, icf_text = load_icf_markdown(args.cancer_type, args.trial_id)
    rng = random.Random(args.seed)
    element_counts = sample_elements(definitions, args.questions_per_trial, rng)
    output_path = args.output or Path(__file__).parent / f"{args.trial_id}_questions.json"

    prompts: list[tuple[str, int, str]] = []
    for element, count in sorted(element_counts.items(), key=lambda item: (-item[1], item[0])):
        prompt_text = build_question_prompt(
            trial_id=args.trial_id,
            cancer_type=args.cancer_type,
            icf_text=icf_text,
            definitions=definitions,
            element=element,
            count=count,
        )
        prompts.append((element, count, prompt_text))

    if args.dry_run:
        print(f"SYSTEM PROMPT:\n{SYSTEM_PROMPT}\n")
        print(f"ICF PATH: {icf_path}")
        print(f"OUTPUT PATH: {output_path}")
        print("SAMPLED ELEMENT COUNTS:")
        for element, count, _prompt_text in prompts:
            print(f"  {element}: {count}")
        print("\nUSER PROMPTS:\n")
        for index, (element, count, prompt_text) in enumerate(prompts, start=1):
            print(f"--- Prompt {index}: {element} ({count}) ---")
            print(prompt_text)
            print()
        return

    result = []
    for _element, _count, prompt_text in prompts:
        result.extend(run_generation(prompt_text))

    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote questions: {output_path}")


if __name__ == "__main__":
    main()
