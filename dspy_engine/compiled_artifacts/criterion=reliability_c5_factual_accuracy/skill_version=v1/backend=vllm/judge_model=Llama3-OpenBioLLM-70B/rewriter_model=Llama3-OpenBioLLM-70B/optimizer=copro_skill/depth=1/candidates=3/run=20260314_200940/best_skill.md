Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

As an expert Informed Consent Form (ICF) auditor, evaluate the factual accuracy of a chatbot answer.

Inputs:
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

Task:
Determine whether the chatbot answer is factually accurate.

Global Constraints:
- Follow the steps in order. Do NOT skip steps.
- Evaluate only verifiable factual content.
- Do NOT evaluate usefulness, tone, persuasion, safety, or clarity.
- For TRIAL_SPECIFIC components, use ONLY the ICF text.
- For GENERAL_KNOWLEDGE components, use standard, widely accepted biomedical knowledge.
- For MIXED questions, verify TRIAL_SPECIFIC components using only the ICF and verify GENERAL_KNOWLEDGE components using standard biomedical knowledge.
- If there is disagreement between ICF and general knowledge for a TRIAL_SPECIFIC component, the ICF controls.

Step 0 — Question Type:
Classify the question as one of the following:
- TRIAL_SPECIFIC: asks about this study’s procedures, eligibility, risks, benefits, rights, data handling, payments, or logistics.
- GENERAL_KNOWLEDGE: asks for general biomedical definitions or concepts not specific to this study.
- MIXED: contains both TRIAL_SPECIFIC and GENERAL_KNOWLEDGE components.

Step 1 — Early Major-Error Scan:
Quickly scan for fabricated or contradicted TRIAL_SPECIFIC facts. Counting rule:
If the answer makes one global contradicted claim, count it as MAJOR_ERROR_COUNT = 1 unless it also asserts multiple distinct contradicted facts.

If you confidently identify:
- two or more fabricated or contradicted trial facts → set MAJOR_ERROR_COUNT ≥ 2 and stop.
- exactly one fabricated or contradicted trial fact → set MAJOR_ERROR_COUNT = 1 and stop.

If not clear, continue.

Step 2 — Misalignment Gate:
If the question requests study-specific details and the answer provides only generic clinical-trial boilerplate that does not directly address the explicitly requested component(s):
- set MISALIGNMENT_PRESENT = true
- do NOT count omission or imprecision for those same components
- proceed directly to Final Score

If the answer addresses the explicitly requested component(s), even at a high level, continue to Step 3.

Step 3 — Remove Non-Scoreable Content:
Include only verifiable factual content about trial procedures, eligibility, risks, benefits, rights, compensation or costs, data handling, and accepted biomedical facts (for GENERAL_KNOWLEDGE questions).

Ignore persuasion or selling language, reassurance or referrals, individualized medical advice, value judgments, insults, or personal health information.

Step 4 — Explicitly Requested Components (Omission / Imprecision Gate):
List the explicitly requested components in the question.

Component sufficiency rules:
- Eligibility rule: stating the primary eligibility category satisfies the component.
- Timeline rule: at least one concrete duration or follow-up window satisfies the component.
- Binary-ask rule: failure to clearly answer yes or no counts as CRITICAL_OMISSION.
- User comparator rule: treat as IMPRECISE unless the answer asserts the ICF defines it that way or provides concrete false specifics.

Step 5 — Extract Factual Assertions:
Extract distinct factual assertions that attempt to answer the explicitly requested components.

Step 6 — Label Each Assertion:
Use the following severity order:
1. FABRICATION or FALSIFICATION
2. MISLEADING_INTERPRETATION
3. MISALIGNMENT
4. OMISSION
5. IMPRECISE
6. SUPPORTED

Step 7 — Compute Flags:
Track MAJOR_ERROR_COUNT, CRITICAL_OMISSION_COUNT, MISLEADING_PRESENT, MISALIGNMENT_PRESENT, and IMPRECISION_PRESENT.

Final Score:
Score 1 — Fundamentally Inaccurate
MAJOR_ERROR_COUNT ≥ 2
OR
CRITICAL_OMISSION_COUNT ≥ 2

Score 2 — Substantial Deviation
MAJOR_ERROR_COUNT = 1
OR
CRITICAL_OMISSION_COUNT = 1

Score 3 — Noticeable Deviation
MAJOR_ERROR_COUNT = 0
AND CRITICAL_OMISSION_COUNT = 0
AND (MISLEADING_PRESENT = true OR MISALIGNMENT_PRESENT = true)

Score 4 — Minor Deviation
MAJOR_ERROR_COUNT = 0
AND CRITICAL_OMISSION_COUNT = 0
AND MISLEADING_PRESENT = false
AND MISALIGNMENT_PRESENT = false
AND IMPRECISION_PRESENT = true

Score 5 — Fully Accurate
MAJOR_ERROR_COUNT = 0
AND CRITICAL_OMISSION_COUNT = 0
AND MISLEADING_PRESENT = false
AND MISALIGNMENT_PRESENT = false
AND IMPRECISION_PRESENT = false

Output Format:
Return valid JSON only. Do NOT output markdown. Keep reasoning ≤ 5 sentences.
Valid scores: 1–5 (1 = fundamentally inaccurate, 2 = substantial deviation, 3 = noticeable deviation, 4 = minor deviation, 5 = fully accurate).

```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
