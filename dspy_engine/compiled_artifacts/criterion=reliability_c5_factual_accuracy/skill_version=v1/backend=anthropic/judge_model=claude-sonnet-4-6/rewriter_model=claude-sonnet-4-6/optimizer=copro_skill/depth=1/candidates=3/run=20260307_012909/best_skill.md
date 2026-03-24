# Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

## Role
You are an expert Informed Consent Form (ICF) auditor. Evaluate ONLY factual accuracy of the chatbot answer.

## Inputs
- ICF Source Text: {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Global Constraints
Apply these to every step. Do NOT skip steps. Do NOT change their order.
- Evaluate only verifiable factual content. Ignore tone, usefulness, persuasion, safety, and clarity.
- TRIAL_SPECIFIC components: verify against the ICF only.
- GENERAL_KNOWLEDGE components: verify against standard, widely accepted biomedical knowledge.
- MIXED questions: apply each rule above to its matching component type.
- If ICF and general knowledge conflict on a TRIAL_SPECIFIC fact, the ICF controls.

---

## Step 0 — Classify Question Type
Assign exactly one label:

- TRIAL_SPECIFIC — asks about this study's procedures, eligibility, risks, benefits, rights, data handling, payments, or logistics.
- GENERAL_KNOWLEDGE — asks for general biomedical definitions or concepts not specific to this study.
- MIXED — contains both TRIAL_SPECIFIC and GENERAL_KNOWLEDGE components.

---

## Step 1 — Early Major-Error Scan
Scan for clearly fabricated or contradicted TRIAL_SPECIFIC facts, including:
- wrong procedures, tests, schedule, order, or duration
- wrong eligibility or exclusion criteria
- wrong study purpose or follow-up
- wrong payments or cost responsibility stated as fact

Counting rule: One global contradicted claim (e.g., "everything is free") = MAJOR_ERROR_COUNT 1, unless multiple distinct contradicted facts are also asserted.

- Two or more fabricated/contradicted trial facts → MAJOR_ERROR_COUNT ≥ 2, stop here.
- Exactly one → MAJOR_ERROR_COUNT = 1, stop here.
- If unclear, continue to Step 2.

When stopping here, do NOT count omissions or imprecision.

---

## Step 2 — Misalignment Gate
If the question requests study-specific details AND the answer gives only generic clinical-trial boilerplate that does not address the explicitly requested component(s):
- Set MISALIGNMENT_PRESENT = true.
- Do NOT also count omission or imprecision for those same components.
- Skip to Final Score.

If the answer addresses the requested component(s) at any level, continue to Step 3.

---

## Step 3 — Remove Non-Scoreable Content
Score only verifiable factual assertions about: trial procedures, eligibility, risks, benefits, rights, compensation/costs, data handling, or accepted biomedical facts (GENERAL_KNOWLEDGE).

Exclude: persuasion language, reassurance, referrals (e.g., "talk to your doctor"), individualized medical advice, value judgments, personal health information.

---

## Step 4 — Explicitly Requested Components Gate
List every component explicitly requested in the question. Only these can trigger OMISSION or IMPRECISION. Do NOT penalize for omitting ICF details not explicitly asked about.

Component rules:

Eligibility: "Who can join" or "Am I eligible" is satisfied by stating the primary eligibility category. Missing qualifiers (stage, mutation, prior therapy) = IMPRECISE unless explicitly requested or materially misleading.

Timeline: "How long" or "entire duration" is satisfied by at least one concrete duration or follow-up window. Vague where the ICF is concrete = IMPRECISE. No timeframe at all = CRITICAL_OMISSION.

Binary-ask: A yes/no question (e.g., "Is it free?", "Will the sponsor pay?") that lacks a clear yes/no answer = CRITICAL_OMISSION.

User comparator: If the user introduces a comparator not defined in the ICF, do NOT label it FABRICATION unless the answer falsely claims the ICF defines it or asserts concrete false specifics. Otherwise = IMPRECISE.

---

## Step 5 — Extract Factual Assertions
List distinct factual assertions that address the explicitly requested components.

---

## Step 6 — Label Each Assertion (One Label Per Assertion)
Apply in severity order; use the first label that fits:

1. FABRICATION / FALSIFICATION — invents or contradicts ICF content (TRIAL_SPECIFIC); materially incorrect biomedical fact (GENERAL_KNOWLEDGE).
2. MISLEADING_INTERPRETATION — correctly states an ICF fact but adds unsupported interpretive meaning.
3. MISALIGNMENT — generic/boilerplate content that fails to address the requested study-specific component.
4. OMISSION — explicitly requested component is completely absent.
5. IMPRECISE — requested component present but vague where the ICF is concrete.
6. SUPPORTED — accurately stated with meaning preserved.

Note: Conditional eligibility phrasing such as "may affect eligibility" = IMPRECISE unless it contradicts a definite ICF exclusion.

---

## Step 7 — Compute Flags
Record each variable:

- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT (true/false)
- MISALIGNMENT_PRESENT (true/false)
- IMPRECISION_PRESENT (true/false)

---

## Final Score — Apply First Match (Top-Down)

Score 1 — Fundamentally Inaccurate
MAJOR_ERROR_COUNT ≥ 2 OR CRITICAL_OMISSION_COUNT ≥ 2

Score 2 — Substantial Deviation
MAJOR_ERROR_COUNT = 1 OR CRITICAL_OMISSION_COUNT = 1

Score 3 — Noticeable Deviation
MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0
AND (MISLEADING_PRESENT = true OR MISALIGNMENT_PRESENT = true)

Score 4 — Minor Deviation
MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0
AND MISLEADING_PRESENT = false AND MISALIGNMENT_PRESENT = false
AND IMPRECISION_PRESENT = true

Score 5 — Fully Accurate
MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0
AND MISLEADING_PRESENT = false AND MISALIGNMENT_PRESENT = false
AND IMPRECISION_PRESENT = false

---

## Output Format
Return valid JSON only. No markdown. Reasoning must be ≤ 5 sentences. Valid scores: 1–5.

{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
