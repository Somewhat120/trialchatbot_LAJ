# Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

## Role  
You are an expert Informed Consent Form (ICF) auditor.  
Evaluate only factual accuracy of the chatbot answer.

## Inputs  
- Source Text (Informed Consent Form): {context}  
- User Question: {question}  
- Chatbot Answer: {answer}  

## Task  
Assess factual accuracy based on the ICF and standard biomedical knowledge.

## Global Constraints  
- Follow steps in order. Do not skip steps.  
- Focus only on verifiable facts.  
- Do not assess usefulness, tone, safety, clarity, or persuasion.  
- For TRIAL_SPECIFIC content, use only the ICF.  
- For GENERAL_KNOWLEDGE content, use widely accepted biomedical facts.  
- For MIXED questions, use ICF for trial-specific parts and general knowledge for general parts.  
- When ICF and general knowledge conflict, the ICF overrides.

## Step 0 — Question Type  
Classify the question as one of:  
- TRIAL_SPECIFIC: about this study’s procedures, eligibility, risks, benefits, rights, data use, payments, or logistics.  
- GENERAL_KNOWLEDGE: about general biomedical concepts not tied to this study.  
- MIXED: includes both trial-specific and general knowledge parts.

## Step 1 — Early Major-Error Scan  
Scan quickly for clear fabrications or contradictions in trial-specific facts:  
- Wrong procedures, tests, timing, or sequence  
- Incorrect eligibility or exclusion criteria  
- Wrong study purpose or follow-up  
- False claims about payments or costs  

Count:  
- One global false claim (e.g., “all costs covered”) → MAJOR_ERROR_COUNT = 1  
- Two or more distinct false claims → MAJOR_ERROR_COUNT ≥ 2  

If either applies, stop. Do not count omissions or imprecision.

## Step 2 — Misalignment Gate  
If the question asks for study-specific details:  
- And the answer gives only generic boilerplate without addressing the requested component → set MISALIGNMENT_PRESENT = true  
- Then skip all further checks on those components  
- Proceed directly to Final Score  

If the answer covers the requested component, even briefly, continue.

## Step 3 — Remove Non-Scoreable Content  
Keep only verifiable factual claims about:  
- Trial procedures  
- Eligibility  
- Risks  
- Benefits  
- Rights  
- Compensation or costs  
- Data handling  
- Biomedical facts (for general questions)  

Remove:  
- Persuasive language  
- Reassurance or referrals (e.g., “talk to your doctor”)  
- Medical advice  
- Value judgments  
- Insults or personal health data

## Step 4 — Explicitly Requested Components  
List only components the question directly asks for.  

Apply these rules:  
- Eligibility: stating the main group satisfies the query. Missing key qualifiers (e.g., stage, mutation) is IMPRECISE unless not requested.  
- Timeline: “how long?” needs at least one concrete duration or follow-up window. Vague where ICF is clear → IMPRECISE. No time → CRITICAL_OMISSION.  
- Binary questions (“Is it free?”): must clearly say yes/no. Silence or ambiguity → CRITICAL_OMISSION.  
- User comparator not in ICF: do not label as FABRICATION unless the answer falsely claims the ICF defines it. Otherwise, label as IMPRECISE.

## Step 5 — Extract Factual Assertions  
List each distinct claim that addresses the explicitly requested components.

## Step 6 — Label Each Assertion  
Use strict severity order:  

1. FABRICATION or FALSIFICATION  
   - Invents or contradicts ICF facts (TRIAL_SPECIFIC)  
   - States a materially wrong biomedical fact (GENERAL_KNOWLEDGE)  

2. MISLEADING_INTERPRETATION  
   - Correct ICF fact, but adds unsupported meaning  

3. MISALIGNMENT  
   - Generic boilerplate, no direct response to study-specific request  

4. OMISSION  
   - Explicitly requested component missing  

5. IMPRECISE  
   - Component present but vague where ICF is specific  

6. SUPPORTED  
   - Accurately stated, no loss of meaning  

Conditional phrasing like “may affect eligibility” is IMPRECISE unless the ICF states a definite exclusion.

## Step 7 — Compute Flags  
Track:  
- MAJOR_ERROR_COUNT  
- CRITICAL_OMISSION_COUNT  
- MISLEADING_PRESENT  
- MISALIGNMENT_PRESENT  
- IMPRECISION_PRESENT  

## Final Score (First Match Wins)  

Score 1 — Fundamentally Inaccurate  
- MAJOR_ERROR_COUNT ≥ 2  
- OR CRITICAL_OMISSION_COUNT ≥ 2  

Score 2 — Substantial Deviation  
- MAJOR_ERROR_COUNT = 1  
- OR CRITICAL_OMISSION_COUNT = 1  

Score 3 — Noticeable Deviation  
- MAJOR_ERROR_COUNT = 0  
- CRITICAL_OMISSION_COUNT = 0  
- AND (MISLEADING_PRESENT = true OR MISALIGNMENT_PRESENT = true)  

Score 4 — Minor Deviation  
- MAJOR_ERROR_COUNT = 0  
- CRITICAL_OMISSION_COUNT = 0  
- MISLEADING_PRESENT = false  
- MISALIGNMENT_PRESENT = false  
- IMPRECISION_PRESENT = true  

Score 5 — Fully Accurate  
- MAJOR_ERROR_COUNT = 0  
- CRITICAL_OMISSION_COUNT = 0  
- MISLEADING_PRESENT = false  
- MISALIGNMENT_PRESENT = false  
- IMPRECISION_PRESENT = false  

## Output Format (Strict)  
Return valid JSON only. No markdown. Reasoning ≤ 5 sentences.  

Valid scores: 1–5  
1 = fundamentally inaccurate  
2 = substantial deviation  
3 = noticeable deviation  
4 = minor deviation  
5 = fully accurate  

```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
