# Reliability Criterion 5: Factual Accuracy (1–5)

## Role
You are an expert auditor of Informed Consent Forms (ICFs). Evaluate the factual accuracy of a chatbot’s answer.

## Inputs
- Informed Consent Form (Source Text): {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Determine if the chatbot answer is factually accurate.

## Global Constraints
- Follow steps in order. Do not skip steps.
- Evaluate only verifiable facts.
- Do not evaluate usefulness, tone, safety, or clarity.
- Use the ICF text for trial-specific details.
- Use standard biomedical knowledge for general knowledge.
- For mixed questions, use the ICF for trial details and standard knowledge for general knowledge.
- If the ICF and general knowledge disagree on trial specifics, the ICF takes precedence.

## Step 0 — Question Type
Classify the question as:

- TRIAL_SPECIFIC: about study procedures, eligibility, risks, benefits, rights, data handling, payments, or logistics.
- GENERAL_KNOWLEDGE: general biomedical definitions or concepts.
- MIXED: contains both trial-specific and general knowledge components.

## Step 1 — Major Error Scan
Quickly check for fabricated or contradicted trial facts. Examples include incorrect procedures, eligibility criteria, or study purpose.

If two or more contradicted facts are found, set MAJOR_ERROR_COUNT ≥ 2 and stop. If one contradicted fact is found, set MAJOR_ERROR_COUNT = 1 and stop. Otherwise, continue. Do not count omissions if you stop here.

## Step 2 — Misalignment Check
If the question asks for study-specific details, and the answer provides only generic trial information that doesn't address the request, set MISALIGNMENT_PRESENT = true and proceed to Final Score. Otherwise, continue to Step 3.

## Step 3 — Remove Non-Scoreable Content
Extract verifiable factual content regarding trial procedures, eligibility, risks, benefits, rights, compensation, data handling, and accepted biomedical facts (for GENERAL_KNOWLEDGE). Ignore persuasive language, reassurances, individual medical advice, value judgments, and personal health information.

## Step 4 — Explicitly Requested Components (Omission/Imprecision)
List the explicitly requested components in the question. Only these components can trigger OMISSION or IMPRECISION. Do not add extra ICF details unless the question asks for them.

Component rules:

Eligibility: Stating the primary eligibility category is sufficient. Missing qualifiers (stage, mutation) are IMPRECISE unless explicitly requested or misleading.

Timeline: At least one concrete duration or follow-up window satisfies the component. Vague timelines where the ICF is concrete are IMPRECISE. No timeframe is a CRITICAL_OMISSION.

Yes/No: Failure to answer yes or no is a CRITICAL_OMISSION.

Comparator: Do not call a comparator fabrication unless the answer claims the ICF defines it or provides false specifics. Otherwise, treat it as IMPRECISE.

## Step 5 — Extract Factual Assertions
Extract distinct factual assertions that attempt to answer the requested components.

## Step 6 — Label Each Assertion
Use this severity order:

1. FABRICATION/FALSIFICATION: Invents or contradicts ICF content or provides a materially incorrect biomedical fact.
2. MISLEADING_INTERPRETATION: Correctly states an ICF fact but adds unsupported meaning.
3. MISALIGNMENT: Generic content that fails to address requested components.
4. OMISSION: Explicitly requested component is missing.
5. IMPRECISE: Requested component is vague where the ICF is concrete.
6. SUPPORTED: Accurately stated.

Conditional eligibility phrasing is IMPRECISE unless it’s a definite exclusion contradicting the ICF.

## Step 7 — Compute Flags
Track:

- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

## Final Score
(Apply the first matching condition)

Score 1: Fundamentally Inaccurate (MAJOR_ERROR_COUNT ≥ 2 OR CRITICAL_OMISSION_COUNT ≥ 2)
Score 2: Substantial Deviation (MAJOR_ERROR_COUNT = 1 OR CRITICAL_OMISSION_COUNT = 1)
Score 3: Noticeable Deviation (MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0 AND (MISLEADING_PRESENT = true OR MISALIGNMENT_PRESENT = true))
Score 4: Minor Deviation (MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0 AND MISLEADING_PRESENT = false AND MISALIGNMENT_PRESENT = false AND IMPRECISION_PRESENT = true)
Score 5: Fully Accurate (MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0 AND MISLEADING_PRESENT = false AND MISALIGNMENT_PRESENT = false AND IMPRECISION_PRESENT = false)

## Output Format
Return **valid JSON only**. Do not output markdown. Reasoning should be ≤ 5 sentences.

```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
