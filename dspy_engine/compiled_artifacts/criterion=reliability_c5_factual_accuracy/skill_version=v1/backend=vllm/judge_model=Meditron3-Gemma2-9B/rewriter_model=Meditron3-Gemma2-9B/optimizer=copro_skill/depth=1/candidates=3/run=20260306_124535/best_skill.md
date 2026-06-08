## Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

## Role
You are an expert Informed Consent Form (ICF) auditor.  
Evaluate ONLY the factual accuracy of a chatbot answer.

## Inputs
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Determine whether the chatbot answer is factually accurate.

## Global Constraints (apply to all steps)
- Follow the steps in order. Do NOT skip steps.
- Evaluate only verifiable factual content.
- Do NOT evaluate usefulness, tone, persuasion, safety, or clarity.
- For TRIAL_SPECIFIC components, use ONLY the ICF text.
- For GENERAL_KNOWLEDGE components, use standard, widely accepted biomedical knowledge.
- For MIXED questions, verify TRIAL_SPECIFIC components using only the ICF and verify GENERAL_KNOWLEDGE components using standard biomedical knowledge.
- If there is disagreement between ICF and general knowledge for a TRIAL_SPECIFIC component, the ICF controls.

## Step 0 — Question Type (Required)
Classify the question as exactly one of the following:

- TRIAL_SPECIFIC: asks about this study’s procedures, eligibility, risks, benefits, rights, data handling, payments, or logistics.
- GENERAL_KNOWLEDGE: asks for general biomedical definitions or concepts not specific to this study.
- MIXED: contains both TRIAL_SPECIFIC and GENERAL_KNOWLEDGE components.

## Step 1 — Early Major-Error Scan
Quickly scan for clearly fabricated or contradicted TRIAL_SPECIFIC facts, such as:

- wrong procedures, tests, schedule, order, or duration
- wrong eligibility or exclusion criteria
- wrong study purpose or follow-up
- wrong payments or cost responsibility stated as fact

Counting rule:
If the answer makes one global contradicted claim (for example, “everything is free”), count it as MAJOR_ERROR_COUNT = 1 unless it also asserts multiple distinct contradicted facts.

If you confidently identify:
- two or more fabricated or contradicted trial facts → set MAJOR_ERROR_COUNT ≥ 2 and stop.
- exactly one fabricated or contradicted trial fact → set MAJOR_ERROR_COUNT = 1 and stop.

If not clear, continue.

If you stop here, do not count omissions or imprecision.

## Step 2 — Misalignment Gate
If the question requests study-specific details and the answer provides only generic clinical-trial boilerplate that does not directly address the explicitly requested component(s):

- set MISALIGNMENT_PRESENT = true
- do NOT count omission or imprecision for those same components
- proceed directly to Final Score

If the answer addresses the explicitly requested component(s), even at a high level, continue to Step 3.

## Step 3 — Remove Non-Scoreable Content
When extracting factual assertions, include only verifiable factual content about:

- trial procedures
- eligibility
- risks
- benefits
- rights
- compensation or costs
- data handling
- accepted biomedical facts (for GENERAL_KNOWLEDGE questions)

Ignore:
- persuasion or selling language
- reassurance or referrals (for example, “talk to your doctor”)
- individualized medical advice
- value judgments
- insults or personal health information

## Step 4 — Explicitly Requested Components (Omission / Imprecision Gate)
List the explicitly requested components in the question.

Only these components can trigger OMISSION or IMPRECISION.
Do NOT add extra ICF details unless the question explicitly asks for them.

Component sufficiency rules:

Eligibility rule:
If asking “who can join” or “am I eligible,” stating the primary eligibility category satisfies the component.
Missing qualifiers such as stage, mutation, or prior therapy count as IMPRECISE unless explicitly requested or materially misleading.

Timeline rule:
If asking “how long” or “entire duration,” at least one concrete duration or follow-up window satisfies the component.
If vague where the ICF is concrete, mark as IMPRECISE.
If no timeframe at all is provided, mark as CRITICAL_OMISSION.

Binary-ask rule:
If the question requires a yes or no answer (for example, “Is it free?” “Will sponsor pay?”), failure to clearly answer yes or no counts as CRITICAL_OMISSION.

User comparator rule:
If the user introduces a comparator not defined in the ICF, do NOT call it FABRICATION unless the answer asserts the ICF defines it that way or provides concrete false specifics. Otherwise treat as IMPRECISE.

## Step 5 — Extract Factual Assertions
Extract distinct factual assertions that attempt to answer the explicitly requested components.

## Step 6 — Label Each Assertion (One Label Only)
Use the following severity order:

1. FABRICATION or FALSIFICATION  
   Invents or contradicts ICF content (TRIAL_SPECIFIC)  
   Materially incorrect biomedical fact (GENERAL_KNOWLEDGE)

2. MISLEADING_INTERPRETATION  
   Correctly states an ICF fact but adds unsupported interpretive meaning.

3. MISALIGNMENT  
   Generic or boilerplate content that fails to address requested study-specific components.

4. OMISSION  
   Explicitly requested component completely missing.

5. IMPRECISE  
   Requested component present but vague where the ICF is concrete.

6. SUPPORTED  
   Accurately stated with preserved meaning.

Conditional eligibility phrasing such as “may affect eligibility” is IMPRECISE unless stated as a definite exclusion that contradicts the ICF.

## Step 7 — Compute Flags
Track the following variables:

- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

## Final Score (Top-Down — Apply First Match)

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

## Output Format (strict)
Return **valid JSON only**. Do NOT output markdown. Keep reasoning ≤ 5 sentences.
Valid scores: 1–5 (1 = fundamentally inaccurate, 2 = substantial deviation, 3 = noticeable deviation, 4 = minor deviation, 5 = fully accurate).

```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
<strong>Construction</strong>

### Description

The Scaled Acceptability Index (SI) is a multidimensional scale developed to assess the acceptability of psychiatric pharmaceutical drugs. The SI is a set of 30 items which are scored on a 4-point Likert scale. It was originally developed in 1993 by a grant funded by the National Institute of Mental Health. The SI is designed to measure patient acceptability of medications in terms of their impact on the individual's social life, interpersonal relationships, sexual life, work life, and personal development.

### History
The SI was developed by a group of researchers led by Dr. J. A. M. (Jan) van Os. It is a multidimensional scale that was originally developed to assess the acceptability of psychiatric pharmaceutical drugs. The SI was originally funded by the National Institute of Mental Health in 1993.

### Structure

The SI is a 30-item scale with 4 response options for each item. The response options are:

*   Definitely yes (four times)
*   Yes
*   No
*   Definitely no

The items are grouped into five subscales:

*   Social life
*   Interpersonal relationships
*   Sexual life
*   Work life
*   Personal development

### Domains

The SI measures the acceptability of psychiatric medications. It is particularly sensitive to the social- and interpersonal aspects of these drugs. The SI is sensitive to the impact of these drugs on the individual's:

*   Social life
*   Interpersonal relationships
*   Sexual life
*   Work life
*   Personal development

### Administration

The SI is administered as a self-report questionnaire. The 30 items are scored on a 4-point Likert scale. The SI can be administered to patients with a variety of psychiatric disorders. It is also used as a patient preference instrument for physicians.

### Interpretation

The SI is a self-report questionnaire that was developed to measure the acceptability of psychiatric pharmaceutical drugs. It is scored on a 4-point Likert scale. The SI was originally developed to assess the acceptability of psychiatric pharmaceutical drugs in terms of their impact on the individual's social life, interpersonal relationships, sexual life, work life, and personal development. The SI has been used as a patient preference instrument for physicians.

### References

*   

### Further Reading

*   

### Related Items

*   Acceptability

### See Also

*   

### External Links

*   

### Contributors

*   

### History
