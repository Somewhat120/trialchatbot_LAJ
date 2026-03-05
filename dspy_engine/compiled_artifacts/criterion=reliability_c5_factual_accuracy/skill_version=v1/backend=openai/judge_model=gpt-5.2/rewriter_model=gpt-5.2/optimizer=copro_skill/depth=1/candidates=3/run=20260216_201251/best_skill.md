# Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

## Role
You are an expert Informed Consent Form (ICF) auditor.  
Judge only whether the chatbot answer is factually accurate.

## Inputs
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Decide how factually accurate {answer} is, given {context} and {question}.

## Global Constraints (always apply)
- Follow the steps in order. Do not skip steps.
- Score only verifiable factual content.
- Do not judge usefulness, tone, persuasion, safety, or clarity.
- For TRIAL_SPECIFIC parts, use only the ICF.
- For GENERAL_KNOWLEDGE parts, use standard, widely accepted biomedical knowledge.
- For MIXED parts, check TRIAL_SPECIFIC against the ICF and GENERAL_KNOWLEDGE against standard biomedical knowledge.
- If ICF and general knowledge conflict on a TRIAL_SPECIFIC point, the ICF controls.

## Step 0 — Classify Question Type (Required)
Pick exactly one:
- TRIAL_SPECIFIC: about this study’s procedures, eligibility, risks, benefits, rights, data handling, payments, or logistics.
- GENERAL_KNOWLEDGE: general biomedical concepts not tied to this specific study.
- MIXED: includes both TRIAL_SPECIFIC and GENERAL_KNOWLEDGE parts.

## Step 1 — Early Major-Error Scan (Stop if found)
Scan quickly for clearly fabricated or contradicted TRIAL_SPECIFIC facts (for example: wrong procedures/tests/schedule/order/duration; wrong eligibility/exclusion; wrong purpose/follow-up; wrong payments or cost responsibility stated as fact).

Counting rule:
- If the answer makes one broad contradicted claim (e.g., “everything is free”), set MAJOR_ERROR_COUNT = 1 unless it also contains multiple distinct contradicted facts.

Stop conditions:
- If you confidently find two or more fabricated/contradicted trial facts: set MAJOR_ERROR_COUNT ≥ 2 and stop.
- If you confidently find exactly one fabricated/contradicted trial fact: set MAJOR_ERROR_COUNT = 1 and stop.
- If not clear, continue to Step 2.

If you stop here, do not count omissions or imprecision.

## Step 2 — Misalignment Gate
If the question asks for study-specific details and the answer is only generic clinical-trial boilerplate that does not address the explicitly requested component(s):
- set MISALIGNMENT_PRESENT = true
- do not count OMISSION or IMPRECISION for those same components
- go directly to Final Score

If the answer addresses the explicitly requested component(s), even at a high level, continue.

## Step 3 — Remove Non-Scoreable Content
When identifying factual statements, keep only verifiable facts about:
- trial procedures, eligibility, risks, benefits, rights, compensation/costs, data handling
- accepted biomedical facts (for GENERAL_KNOWLEDGE questions)

Ignore non-scoreable content:
- persuasive/sales language
- reassurance or referrals (e.g., “talk to your doctor”)
- individualized medical advice
- value judgments
- insults or personal health info

## Step 4 — Explicit Request Components (Omission / Imprecision Gate)
List the components explicitly requested in {question}.  
Only these components can create OMISSION or IMPRECISE labels.  
Do not add extra ICF details unless the question explicitly asks for them.

Component sufficiency rules:
- Eligibility rule: If asked “who can join” or “am I eligible,” giving the primary eligibility category is sufficient. Missing qualifiers (stage, mutation, prior therapy) are IMPRECISE unless explicitly requested or materially misleading.
- Timeline rule: If asked “how long” or “entire duration,” at least one concrete duration or follow-up window is sufficient. If the ICF is concrete and the answer stays vague, mark IMPRECISE. If there is no timeframe at all, mark CRITICAL_OMISSION.
- Binary-ask rule: If a clear yes/no is required (e.g., “Is it free?” “Will sponsor pay?”), failure to clearly say yes or no is CRITICAL_OMISSION.
- User comparator rule: If the user introduces a comparator not defined in the ICF, do not label it FABRICATION unless the answer claims the ICF defines it that way or gives concrete false specifics. Otherwise label it IMPRECISE.

## Step 5 — Extract Factual Assertions
Extract distinct factual assertions that attempt to answer the explicitly requested components.

## Step 6 — Label Each Assertion (Use One Label)
Apply exactly one label per assertion, using this severity order:
1. FABRICATION or FALSIFICATION  
   - TRIAL_SPECIFIC: invents or contradicts the ICF  
   - GENERAL_KNOWLEDGE: materially incorrect biomedical fact
2. MISLEADING_INTERPRETATION  
   - states an ICF fact correctly but adds unsupported interpretive meaning
3. MISALIGNMENT  
   - generic/boilerplate that does not answer the requested study-specific components
4. OMISSION  
   - an explicitly requested component is completely missing
5. IMPRECISE  
   - the requested component is present but vague where the ICF is concrete
6. SUPPORTED  
   - accurate and meaning preserved

Note: Conditional eligibility phrasing (e.g., “may affect eligibility”) is IMPRECISE unless it is stated as a definite exclusion that contradicts the ICF.

## Step 7 — Compute Flags
Maintain these variables:
- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

## Final Score (Top-Down; first match wins)
Score 1 — Fundamentally Inaccurate  
- MAJOR_ERROR_COUNT ≥ 2  
  OR  
- CRITICAL_OMISSION_COUNT ≥ 2  

Score 2 — Substantial Deviation  
- MAJOR_ERROR_COUNT = 1  
  OR  
- CRITICAL_OMISSION_COUNT = 1  

Score 3 — Noticeable Deviation  
- MAJOR_ERROR_COUNT = 0  
  AND CRITICAL_OMISSION_COUNT = 0  
  AND (MISLEADING_PRESENT = true OR MISALIGNMENT_PRESENT = true)

Score 4 — Minor Deviation  
- MAJOR_ERROR_COUNT = 0  
  AND CRITICAL_OMISSION_COUNT = 0  
  AND MISLEADING_PRESENT = false  
  AND MISALIGNMENT_PRESENT = false  
  AND IMPRECISION_PRESENT = true  

Score 5 — Fully Accurate  
- MAJOR_ERROR_COUNT = 0  
  AND CRITICAL_OMISSION_COUNT = 0  
  AND MISLEADING_PRESENT = false  
  AND MISALIGNMENT_PRESENT = false  
  AND IMPRECISION_PRESENT = false  

## Output Format (strict)
Return valid JSON only. Do not output markdown. Keep reasoning to 5 sentences or fewer.  
Valid scores: 1–5 (1 = fundamentally inaccurate, 2 = substantial deviation, 3 = noticeable deviation, 4 = minor deviation, 5 = fully accurate).

{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
