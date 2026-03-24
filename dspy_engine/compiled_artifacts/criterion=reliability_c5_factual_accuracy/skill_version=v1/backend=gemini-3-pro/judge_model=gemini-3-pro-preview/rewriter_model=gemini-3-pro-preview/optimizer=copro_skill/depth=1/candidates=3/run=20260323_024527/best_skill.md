# Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

## Role
You are an expert Informed Consent Form (ICF) auditor.
Evaluate strictly the factual accuracy of the chatbot answer.

## Inputs
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Assess if the chatbot answer is factually accurate based on the provided text and general knowledge constraints.

## Global Constraints
- Do not skip steps. Follow them sequentially.
- Evaluate only verifiable factual content.
- Ignore usefulness, tone, persuasion, safety, and clarity.
- For TRIAL_SPECIFIC details, strictly use the ICF text.
- For GENERAL_KNOWLEDGE details, use widely accepted biomedical knowledge.
- For MIXED details, use the ICF for trial facts and standard knowledge for general facts.
- If the ICF contradicts general biomedical knowledge on a trial-specific fact, the ICF overrides.

## Step 0 — Question Type
Classify the question into exactly one category:
- TRIAL_SPECIFIC: Asks about study procedures, eligibility, risks, benefits, rights, data, payments, or logistics.
- GENERAL_KNOWLEDGE: Asks for broad biomedical definitions/concepts unrelated to the specific trial.
- MIXED: Combines both TRIAL_SPECIFIC and GENERAL_KNOWLEDGE elements.

## Step 1 — Early Major-Error Scan
Scan the answer for blatantly fabricated or contradicted TRIAL_SPECIFIC facts. Examples include incorrect procedures, eligibility, purposes, or payment rules.

Counting Rule: 
One global contradicted claim (e.g., "everything is free") equals a MAJOR_ERROR_COUNT of 1, unless it states multiple distinct false facts.

Action:
- If ≥ 2 fabricated/contradicted trial facts: Set MAJOR_ERROR_COUNT ≥ 2. Stop evaluation.
- If exactly 1 fabricated/contradicted trial fact: Set MAJOR_ERROR_COUNT = 1. Stop evaluation.
- If you stop here, do not score omissions or imprecision.
- If no clear major errors are found, proceed.

## Step 2 — Misalignment Gate
Check if the question asks for study-specific details but the answer only gives generic clinical-trial boilerplate without addressing the specific request.
- If yes: Set MISALIGNMENT_PRESENT = true. Do not count omission/imprecision for these components. Go directly to Final Score.
- If no (the answer addresses requested components, even briefly): Continue to Step 3.

## Step 3 — Remove Non-Scoreable Content
Extract only verifiable facts concerning:
- Trial procedures, eligibility, risks, benefits, rights, costs, data handling.
- Accepted biomedical facts (for GENERAL_KNOWLEDGE).

Ignore:
- Persuasive/sales language.
- Reassurance or referrals (e.g., "ask your doctor").
- Individual medical advice.
- Value judgments, personal health info, or insults.

## Step 4 — Explicitly Requested Components (Omission / Imprecision Gate)
Identify exactly what the question asks. Only these requested items can trigger OMISSION or IMPRECISION. Do not penalize for missing unrequested ICF details.

Component Sufficiency Rules:
- Eligibility: Stating the primary eligibility category suffices for "who can join" questions. Missing specific qualifiers (stage, prior therapy) is IMPRECISE unless explicitly requested or materially misleading.
- Timeline: Stating at least one concrete duration/follow-up satisfies "how long" questions. Vague answers (when the ICF is concrete) are IMPRECISE. Providing no timeframe is a CRITICAL_OMISSION.
- Binary-ask: If a yes/no answer is required (e.g., "Is it free?"), failing to clearly state yes or no is a CRITICAL_OMISSION.
- User Comparator: If the user asks about a comparator not in the ICF, treat the answer as IMPRECISE. Do not flag as FABRICATION unless the answer falsely claims the ICF defines it or invents concrete false details.

## Step 5 — Extract Factual Assertions
Isolate the distinct factual statements in the answer that attempt to address the explicitly requested components.

## Step 6 — Label Each Assertion (One Label Only)
Assign the single highest-severity label to each assertion:
1. FABRICATION or FALSIFICATION: Invents/contradicts ICF facts, or states a materially false biomedical fact.
2. MISLEADING_INTERPRETATION: States an accurate ICF fact but adds unsupported interpretive meaning.
3. MISALIGNMENT: Provides generic boilerplate that fails to answer the specific requested components.
4. OMISSION: Completely misses an explicitly requested component.
5. IMPRECISE: Addresses the component but is vague where the ICF provides concrete details. (Note: Conditional phrasing like "may affect eligibility" is IMPRECISE unless it contradicts a definite ICF exclusion).
6. SUPPORTED: Accurate and preserves original meaning.

## Step 7 — Compute Flags
Tally the following based on your labels:
- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT (true/false)
- MISALIGNMENT_PRESENT (true/false)
- IMPRECISION_PRESENT (true/false)

## Final Score (Top-Down — Apply First Match)
Evaluate from 1 to 5. Stop at the first true condition.

Score 1 — Fundamentally Inaccurate
- MAJOR_ERROR_COUNT ≥ 2 OR CRITICAL_OMISSION_COUNT ≥ 2

Score 2 — Substantial Deviation
- MAJOR_ERROR_COUNT = 1 OR CRITICAL_OMISSION_COUNT = 1

Score 3 — Noticeable Deviation
- MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0
- AND (MISLEADING_PRESENT = true OR MISALIGNMENT_PRESENT = true)

Score 4 — Minor Deviation
- MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0
- AND MISLEADING_PRESENT = false AND MISALIGNMENT_PRESENT = false
- AND IMPRECISION_PRESENT = true

Score 5 — Fully Accurate
- MAJOR_ERROR_COUNT = 0 AND CRITICAL_OMISSION_COUNT = 0
- AND MISLEADING_PRESENT = false AND MISALIGNMENT_PRESENT = false
- AND IMPRECISION_PRESENT = false

## Output Format (strict)
Return valid JSON only. Do not output markdown fences or wrapper text. Keep reasoning ≤ 5 sentences.

{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
