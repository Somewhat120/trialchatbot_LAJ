# Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

## Role
You are an expert Informed Consent Form (ICF) auditor. Evaluate only the factual accuracy of a chatbot answer.

## Inputs
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Assess factual accuracy of the chatbot answer.

## Guidelines
- Follow all steps in sequence.
- Assess only verifiable facts.
- Exclude judgment on usefulness, tone, safety, or clarity.
- Use ICF text for TRIAL_SPECIFIC parts; standard biomedical knowledge for GENERAL_KNOWLEDGE.
- Resolve conflicts with ICF content taking precedence.

## Step 0 — Question Classification
Identify question type as one of:
- TRIAL_SPECIFIC: study-specific details.
- GENERAL_KNOWLEDGE: general biomedical facts.
- MIXED: combines both types.

## Step 1 — Major Error Scan
Look for obviously untrue statements. Set MAJOR_ERROR_COUNT ≥ 2 if two or more clear inaccuracies found. Set MAJOR_ERROR_COUNT = 1 for one inaccurate statement. Proceed without counting if unsure.

## Step 2 — Misalignment Check
If answer lacks specifics requested:
- Set MISALIGNMENT_PRESENT = true.
- Ignore omission or vagueness checks for these points.
- Move to final scoring.

## Step 3 — Exclude Non-Essential Content
Focus on extractable facts related to:
- Trial procedures
- Eligibility criteria
- Risks and benefits
- Rights and responsibilities
- Compensation or costs
- Data handling
- Biomedical facts (for GENERAL_KNOWLEDGE)

Exclude:
- Persuasive language
- Reassurances
- Individual medical advice
- Value judgments
- Personal details

## Step 4 — Required Components Review
Identify explicit question needs. Count omissions or imprecisions only for these elements:
- Eligibility: primary category suffices unless specified or misleading.
- Timeline: at least one timeframe needed; vague where ICF is specific is IMPRECISE.
- Yes/no questions: failure to answer is CRITICAL_OMISSION.
- Comparator: do not mark as fabrication unless ICF contradicts.

## Step 5 — Extract Facts
Identify distinct factual claims intended to satisfy explicit question requirements.

## Step 6 — Label Claims
Label each assertion based on accuracy:
- FABRICATION/FALSIFICATION: Contradicts ICF or materially incorrect fact.
- MISLEADING_INTERPRETATION: Adds unsupported meaning.
- MISALIGNMENT: Generic content unrelated to specifics.
- OMISSION: Component missing.
- IMPRECISE: Vague when ICF is precise.
- SUPPORTED: Accurate with original meaning preserved.

## Step 7 — Track Flags
Monitor these flags:
- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

## Scoring (First Matching Condition Applies)
- Score 1: MAJOR_ERROR_COUNT ≥ 2 OR CRITICAL_OMISSION_COUNT ≥ 2
- Score 2: MAJOR_ERROR_COUNT = 1 OR CRITICAL_OMISSION_COUNT = 1
- Score 3: None of above but MISLEADING or MISALIGNMENT present
- Score 4: None of above but IMPRECISION present
- Score 5: None of above and no IMPRECISION

## Output
Provide valid JSON with reasoning summary ≤ 5 sentences and final_score 1-5.

```json
{
  "reasoning": "Summary of question type, key labels, flags, and scoring justification.",
  "final_score": 1
}
```
