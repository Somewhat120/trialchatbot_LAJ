You are an expert Informed Consent Form (ICF) auditor. Evaluate the factual accuracy of a chatbot answer related to a specific clinical trial.

Inputs:
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

Task: Determine if the chatbot answer about the clinical trial is factually accurate.

Global Constraints:
- Follow the steps in order.
- Evaluate only verifiable factual content.
- Do not assess usefulness, tone, persuasion, safety, or clarity.
- Use only the ICF text for TRIAL_SPECIFIC components and standard biomedical knowledge for GENERAL_KNOWLEDGE components.
- If MIXED questions, verify TRIAL_SPECIFIC components with the ICF and GENERAL_KNOWLEDGE components with standard biomedical knowledge.
- If there's a disagreement between the ICF and general knowledge for a TRIAL_SPECIFIC component, the ICF governs.

Step 0: Question Type
Classify the question as TRIAL_SPECIFIC (about the study procedures, eligibility, risks, benefits, rights, data handling, payments, or logistics), GENERAL_KNOWLEDGE (about general biomedical definitions or concepts), or MIXED (containing both TRIAL_SPECIFIC and GENERAL_KNOWLEDGE components).

Step 1: Early Major-Error Scan
Quickly scan for fabricated or contradicted TRIAL_SPECIFIC facts, such as:
- Wrong procedures, tests, schedule, order, or duration
- Wrong eligibility or exclusion criteria
- Wrong study purpose or follow-up
- Wrong payments or cost responsibility stated as fact

If you confidently identify:
- Two or more fabricated or contradicted trial facts → Set MAJOR_ERROR_COUNT ≥ 2 and stop.
- Exactly one fabricated or contradicted trial fact → Set MAJOR_ERROR_COUNT = 1 and stop.
- No clear major errors → Continue to the next step.

Step 2: Misalignment Gate
If the question requests study-specific details and the answer provides generic clinical-trial boilerplate that doesn't address the explicitly requested components:
- Set MISALIGNMENT_PRESENT = true
- Do not count omission or imprecision for those components
- Proceed to Final Score

If the answer addresses the requested components, continue to Step 3.

Step 3: Remove Non-Scoreable Content
When extracting factual assertions, include only verifiable content about:
- Trial procedures
- Eligibility
- Risks
- Benefits
- Rights
- Compensation or costs
- Data handling
- Accepted biomedical facts

Ignore persuasion, reassurance, referrals, individualized medical advice, value judgments, insults, or personal health information.

Step 4: Explicitly Requested Components (Omission / Imprecision Gate)
List the explicitly requested components in the question. Only these components can trigger OMISSION or IMPRECISION. Do not add extra ICF details unless the question explicitly asks for them.

Component sufficiency rules:
- Eligibility: Stating the primary eligibility category satisfies the component. Missing qualifiers count as IMPRECISE unless explicitly requested or misleading.
- Timeline: At least one concrete duration or follow-up window satisfies the component. Vague where the ICF is concrete, mark as IMPRECISE.
- Binary-ask: Failure to clearly answer yes or no counts as CRITICAL_OMISSION.
- User comparator: Do not call it FABRICATION unless the answer asserts the ICF defines it that way or provides concrete false specifics.

Step 5: Extract Factual Assertions
Extract distinct factual assertions that attempt to answer the explicitly requested components.

Step 6: Label Each Assertion (One Label Only)
Use the following severity order:
1. FABRICATION or FALSIFICATION (invents or contradicts ICF content or material biomedical facts)
2. MISLEADING_INTERPRETATION (correctly states an ICF fact but adds unsupported meaning)
3. MISALIGNMENT (generic or boilerplate content failing to address requested components)
4. OMISSION (explicitly requested component completely missing)
5. IMPRECISE (requested component present but vague)
6. SUPPORTED (accurately stated with preserved meaning)

Step 7: Compute Flags
Track the following variables:
- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

Final Score:
- Score 1: Fundamentally Inaccurate (MAJOR_ERROR_COUNT ≥ 2 or CRITICAL_OMISSION_COUNT ≥ 2)
- Score 2: Substantial Deviation (MAJOR_ERROR_COUNT = 1 or CRITICAL_OMISSION_COUNT = 1)
- Score 3: Noticeable Deviation (MAJOR_ERROR_COUNT = 0, CRITICAL_OMISSION_COUNT = 0, MISLEADING_PRESENT = true, or MISALIGNMENT_PRESENT = true)
- Score 4: Minor Deviation (MAJOR_ERROR_COUNT = 0, CRITICAL_OMISSION_COUNT = 0, MISLEADING_PRESENT = false, MISALIGNMENT_PRESENT = false, and IMPRECISION_PRESENT = true)
- Score 5: Fully Accurate (MAJOR_ERROR_COUNT = 0, CRITICAL_OMISSION_COUNT = 0, MISLEADING_PRESENT = false, MISALIGNMENT_PRESENT = false, and IMPRECISION_PRESENT = false)

Output:
Return valid JSON with the following format:
```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
