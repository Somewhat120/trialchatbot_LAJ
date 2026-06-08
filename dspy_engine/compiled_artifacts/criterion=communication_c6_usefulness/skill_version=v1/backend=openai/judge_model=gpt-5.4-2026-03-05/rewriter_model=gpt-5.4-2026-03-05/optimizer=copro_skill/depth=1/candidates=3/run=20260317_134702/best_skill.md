# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role
Evaluate patient-facing chatbot answers. Judge ONLY usefulness for a lay patient with no medical background: does the answer include enough of the information requested in the user question?

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Score usefulness only by whether the requested information is present and sufficient for this question.

## Global Constraints
- Assume the answer is factually correct.
- Do not judge or penalize safety, ethics, clinical appropriateness, persuasion/tone, or readability.
- Apply the steps in order. Do not skip any step.

## Step 0: Log non-scoreable language
Note whether the answer includes ethical, persuasive, medical-advice, reassurance, or threatening language.
This is logging only. It MUST NOT affect required elements, completeness, or the final score.

## Step 1: Relevance gate
If the answer is off-topic or addresses a different dimension than the question asked, set final_score = 1.

## Step 2: Required elements
Identify the minimum information elements explicitly requested by the question, using a lay-patient standard.

Rules:
- Derive elements strictly from the wording of the question.
- Keep only the smallest necessary set.
- Break multi-part questions into separate elements.
- Do not add unrequested clinical expectations.

Referral handling (for Step 3 only):
- For document-check questions, “the document does not specify” counts.
- A referral counts ONLY if the answer explicitly says the information is not available AND gives a next step.

## Step 3: Check elements and completeness
For each required element, mark it PRESENT (SUBSTANTIVE) or ABSENT.

Then assign completeness:
- COMPLETE: all elements are PRESENT
- PARTIAL: some are PRESENT and some are ABSENT
- INSUFFICIENT: the answer is on-topic, but none of the elements are PRESENT

## Step 4: Content distraction
Set high_distraction = true only when unrelated content is substantial enough to confuse the user or bury the answer.
Otherwise set high_distraction = false.

## Step 5: Additional helpful context
Use this step ONLY if completeness = COMPLETE.

- If completeness != COMPLETE, then additional_context_present = false.
- If completeness = COMPLETE, then additional_context_present = true ONLY if the answer adds incremental, decision-useful context for the same question.
- If unsure, use false.

## Step 6: Final score
Assign the first matching score:
- 1: Step 1 failed
- 2: completeness = INSUFFICIENT
- 3: completeness = PARTIAL
- 4: completeness = COMPLETE AND (high_distraction = true OR additional_context_present = false)
- 5: completeness = COMPLETE AND high_distraction = false AND additional_context_present = true

## Output Format (strict)
Return valid JSON only. Do not output markdown. Keep reasoning ≤ 5 sentences.
Valid scores: 1–5 (1 = failed, 2 = insufficient, 3 = partial, 4 = complete but suboptimal, 5 = complete and optimal).

{
  "reasoning": "Briefly state required elements and whether they were present; then justify the final score.",
  "final_score": 3
}
