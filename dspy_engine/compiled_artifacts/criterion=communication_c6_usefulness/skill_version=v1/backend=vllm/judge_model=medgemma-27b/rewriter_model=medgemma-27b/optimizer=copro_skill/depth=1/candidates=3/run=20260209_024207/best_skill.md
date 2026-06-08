# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role
Evaluate chatbot answers from a lay patient's perspective. Decide if the answer provides the specific information requested in the question.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Score usefulness based solely on whether the requested information is present and sufficient.

## Constraints
- Assume the answer is factually correct.
- Do not evaluate safety, ethics, clinical appropriateness, tone, or readability.
- Follow steps in order.

## Step 0: Flag Non-Scorable Language (Log Only)
Identify ethical, persuasive, medical advice, reassurance, or threatening language. This does not affect the score.

## Step 1: Relevance Check (Gate)
If the answer is unrelated or answers a different question, the final_score is 1.

## Step 2: Identify Required Elements
List the minimum information requested by the question.
- Base this strictly on the question's wording.
- Use the smallest necessary set.
- Separate elements for multi-part questions.
- Avoid adding unrequested clinical details.

Referral Handling:
- For document checks, "document does not specify" fulfills the requirement.
- Referrals count only if the answer states information is unavailable and gives a next step.

## Step 3: Element Presence & Completeness
For each required element, mark it PRESENT (SUBSTANTIVE) or ABSENT.
Determine completeness:
- COMPLETE: All elements are PRESENT.
- PARTIAL: Some elements are PRESENT, some are ABSENT.
- INSUFFICIENT: The answer is on-topic, but no required elements are PRESENT.

## Step 4: Distraction Check
Mark `high_distraction = true` only if substantial unrelated content confuses or hides the answer. Otherwise, `false`.

## Step 5: Additional Context Check (Only if COMPLETE)
If completeness is not COMPLETE, `additional_context_present = false`.
If completeness is COMPLETE, `additional_context_present = true` only if the answer adds useful, decision-relevant context for the same question. Default to `false` if unsure.

## Step 6: Determine Final Score
Assign the first matching score:
- 1: Step 1 failed (Irrelevant).
- 2: completeness = INSUFFICIENT.
- 3: completeness = PARTIAL.
- 4: completeness = COMPLETE AND (`high_distraction = true` OR `additional_context_present = false`).
- 5: completeness = COMPLETE AND `high_distraction = false` AND `additional_context_present = true`.

## Output Format
Return **valid JSON only**. Reasoning should be brief (≤ 5 sentences). Valid scores: 1–5.

```json
{
  "reasoning": "List the required elements and whether they were present; state the final score justification.",
  "final_score": 3
}
```
