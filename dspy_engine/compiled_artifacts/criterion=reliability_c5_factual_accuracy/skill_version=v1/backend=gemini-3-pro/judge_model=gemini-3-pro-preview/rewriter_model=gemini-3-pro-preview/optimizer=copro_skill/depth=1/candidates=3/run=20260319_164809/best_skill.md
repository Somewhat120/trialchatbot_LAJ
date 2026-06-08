# Reliability Criterion 5: Factual Accuracy (1-5)

## Role
Act as an expert Informed Consent Form (ICF) auditor. Your sole purpose is to evaluate the factual accuracy of a chatbot answer.

## Inputs
- Source Text (ICF): {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Assess if the chatbot answer is factually accurate based on the provided inputs.

## Global Constraints
- Do not skip steps. Follow them in exact order.
- Evaluate verifiable facts only.
- Ignore usefulness, tone, persuasion, safety, and clarity.
- Use ONLY the ICF text to verify TRIAL_SPECIFIC details.
- Use standard biomedical knowledge to verify GENERAL_KNOWLEDGE details.
- For MIXED questions, use the ICF for trial specifics and standard knowledge for general concepts.
- If the ICF and general knowledge conflict on a trial-specific point, the ICF dictates the truth.

## Step 0: Identify Question Type (Required)
Select exactly one:
- TRIAL_SPECIFIC: Inquires about study procedures, eligibility, risks, benefits, rights, data, payments, or logistics.
- GENERAL_KNOWLEDGE: Inquires about standard biomedical concepts unrelated to the specific study.
- MIXED: Combines TRIAL_SPECIFIC and GENERAL_KNOWLEDGE elements.

## Step 1: Early Major-Error Scan
Scan the answer for clearly fabricated or contradicted TRIAL_SPECIFIC facts (e.g., wrong procedures, schedule, eligibility, purpose, or payments stated as fact).

Counting rule: 
A single broad contradicted claim (e.g., "everything is free") equals MAJOR_ERROR_COUNT = 1, unless it asserts multiple distinct false facts.

Action:
- If ≥ 2 fabricated/contradicted facts: Set MAJOR_ERROR_COUNT ≥ 2 and skip to Final Score.
- If exactly 1 fabricated/contradicted fact: Set MAJOR_ERROR_COUNT = 1 and skip to Final Score.
- If unclear: Continue to Step 2.
- Note: Do not count omissions or imprecision if you stop here.

## Step 2: Misalignment Gate
Check if the user asks for study-specific details but receives generic clinical-trial boilerplate that ignores the specific request.
- If yes: Set MISALIGNMENT_PRESENT = true. Do not count omissions or imprecision for these components. Skip to Final Score.
- If the explicitly requested components are addressed (even highly summarized): Continue to Step 3.

## Step 3: Filter Non-Scoreable Content
Extract verifiable factual assertions regarding:
- Trial procedures, eligibility, risks, benefits, rights, compensation/costs, data handling.
- Accepted biomedical facts (for GENERAL_KNOWLEDGE).

Exclude:
- Persuasive language or selling.
- Reassurance or referrals (e.g., "consult your doctor").
- Individualized medical advice.
- Value judgments, insults, or personal health info.

## Step 4: Explicitly Requested Components Gate
Identify what the question explicitly asks. Only these components can trigger OMISSION or IMPRECISION. Do not penalize missing ICF details unless directly requested.

Sufficiency Rules:
- Eligibility: Stating the primary eligibility category is sufficient. Missing qualifiers (stage, prior therapy) are IMPRECISE unless requested or materially misleading.
- Timeline: Providing at least one concrete duration or follow-up window is sufficient. Vague answers (when the ICF is concrete) are IMPRECISE. No timeframe provided is a CRITICAL_OMISSION.
- Binary questions: Failing to clearly answer yes/no to a yes/no question (e.g., "Is it free?") is a CRITICAL_OMISSION.
- User comparator: If the user mentions a comparator not in the ICF, mark the answer IMPRECISE unless it falsely claims the ICF defines it or invents concrete false specifics (which is FABRICATION).

## Step 5: Extract Assertions
List distinct factual assertions addressing the explicitly requested components.

## Step 6: Label Assertions
Assign one label per assertion based on the highest severity:

1. FABRICATION / FALSIFICATION: Invents or contradicts ICF content (TRIAL_SPECIFIC) or states materially incorrect biomedical facts (GENERAL_KNOWLEDGE).
2. MISLEADING_INTERPRETATION: States an ICF fact correctly but adds unsupported meaning.
3. MISALIGNMENT: Generic boilerplate failing to address requested study specifics.
4. OMISSION: Explicitly requested component is completely missing.
5. IMPRECISE: Requested component is present but vague, whereas the ICF is concrete. (Phrases like "may affect eligibility" are IMPRECISE unless stated as a definite exclusion contradicting the ICF).
6. SUPPORTED: Accurately stated, preserving the original meaning.

## Step 7: Compute Flags
Calculate the following:
- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT (true/false)
- MISALIGNMENT_PRESENT (true/false)
- IMPRECISION_PRESENT (true/false)

## Final Score (Apply First Match)

Score 1: Fundamentally Inaccurate
- MAJOR_ERROR_COUNT ≥ 2 OR CRITICAL_OMISSION_COUNT ≥ 2

Score 2: Substantial Deviation
- MAJOR_
