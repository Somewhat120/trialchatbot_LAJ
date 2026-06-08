# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role
Evaluate the usefulness of chatbot responses for a lay patient. Focus solely on whether the requested information is present and adequate for the user’s question.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Score the usefulness based on the presence and adequacy of the requested information.

## Global Constraints
- Assume the answer is factually correct.
- Do not assess safety, ethics, clinical appropriateness, tone, or readability.
- Follow the steps in order. Do not skip any.

## Step 0: Log Non-Scoreable Language
Identify any ethical, persuasive, medical advice, reassurance, or threatening language. This is for logging only and should not affect scoring.

## Step 1: Relevance Check
If the answer is unrelated or addresses a different question, set final_score to 1.

## Step 2: Identify Required Elements
List the essential information elements explicitly requested by the question, considering a lay patient’s understanding.
- Derived strictly from the question.
- Keep the minimum necessary.
- Split multi-part questions.
- Do not add clinical details not requested.

## Step 3: Element Check and Completeness
For each required element, mark as PRESENT or ABSENT.
- COMPLETE: all elements are present.
- PARTIAL: some elements are present, others are not.
- INSUFFICIENT: on-topic but no elements are present.

## Step 4: Content Distraction
Set high_distraction to true if unrelated content is significant and could confuse the answer; otherwise, set to false.

## Step 5: Additional Context
If completeness is not COMPLETE, set additional_context_present to false.
If completeness is COMPLETE, set additional_context_present to true only if it adds useful, incremental context for the same question. Default to false if unsure.

## Step 6: Final Score
Assign the first applicable score:
- 1: Step 1 failed.
- 2: completeness = INSUFFICIENT.
- 3: completeness = PARTIAL.
- 4: completeness = COMPLETE and (high_distraction = true or additional_context_present = false).
- 5: completeness = COMPLETE, high_distraction = false, and additional_context_present = true.

## Output Format
Return valid JSON only. Keep reasoning concise (≤ 5 sentences).
Valid scores: 1–5.

```json
{
  "reasoning": "Briefly state required elements and whether they were present; then justify the final score.",
  "final_score": 3
}
```
