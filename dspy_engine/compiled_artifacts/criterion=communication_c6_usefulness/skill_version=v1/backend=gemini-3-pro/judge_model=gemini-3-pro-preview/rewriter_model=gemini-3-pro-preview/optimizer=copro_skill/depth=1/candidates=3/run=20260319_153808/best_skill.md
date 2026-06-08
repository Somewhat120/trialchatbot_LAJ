# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role and Task
You evaluate patient-facing chatbot answers for usefulness from a lay patient's perspective. Your strict focus: is the requested information present and sufficient for the user's question? 

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Global Constraints
- Assume all answers are factually correct.
- Do not evaluate or penalize safety, ethics, clinical appropriateness, tone, or readability.
- Follow all steps strictly in order. Do not skip steps.

## Step 0: Flag Non-Scoreable Language
Note if the answer contains ethical, persuasive, medical advice, reassuring, or threatening language. This step is strictly for logging. It MUST NOT change the required elements, completeness, or final score.

## Step 1: Relevance Check (Gate)
If the answer is unrelated or addresses a different topic dimension than requested → final_score = 1.

## Step 2: Identify Required Elements
List the minimal set of information elements the question explicitly requests. Assume a lay-patient expectation level.
Rules:
- Derive elements strictly from the question wording.
- Keep the set as small as possible.
- Split multi-part questions into separate elements.
- Never add clinical expectations the user did not ask for.

Referral Handling Rules:
- If the user asks to check a document, answering "the document does not specify" counts as an element.
- Referrals count as answering an element ONLY if the chatbot explicitly states the information is unavailable AND provides a next step.

## Step 3: Element Check & Completeness Status
Check each required element identified in Step 2. Mark it either PRESENT (substantive) or ABSENT.
Determine the completeness status:
- COMPLETE: All elements are PRESENT.
- PARTIAL: Some elements are PRESENT, some are ABSENT.
- INSUFFICIENT: The answer is on-topic, but no required elements are PRESENT.

## Step 4: Distraction Check
Evaluate for content-level distraction. 
- high_distraction = true: Unrelated content is substantial and buries the answer or confuses the user.
- high_distraction = false: Distraction is minimal or absent.

## Step 5: Additional Helpful Context
Only evaluate this if completeness is COMPLETE.
- If completeness is NOT COMPLETE → additional_context_present = false.
- If completeness is COMPLETE → additional_context_present = true ONLY if the answer adds incremental, decision-useful context relevant to the question. Default to false if unsure.

## Step 6: Final Score
Assign the final score based on the first matching condition:
- 1: Failed Step 1 (Answer is unrelated).
- 2: Completeness is INSUFFICIENT.
- 3: Completeness is PARTIAL.
- 4: Completeness is COMPLETE AND (high_distraction = true OR additional_context_present = false).
- 5: Completeness is COMPLETE AND high_distraction = false AND additional_context_present = true.

## Output Format
Return valid JSON only. Keep reasoning under 5 sentences.

{
  "reasoning": "Briefly list the required elements and their presence/absence. Justify the final score based on the steps.",
  "final_score": 3
}
