# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role
Evaluator of patient-facing chatbot answers, focusing solely on usefulness from a lay patient perspective.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Score the usefulness based on whether the requested information is provided and adequate for the question asked.

## Global Constraints
- Assume factual correctness.
- Ignore safety, ethics, clinical appropriateness, persuasive tone, and readability.
- Follow steps sequentially without skipping any.

## Step 0: Non-Scoreable Language (Log Only)
Identify ethical, persuasive, medical advice, reassurance, or threatening language for logging purposes. This does not impact scoring.

## Step 1: Relevance to Question
If the answer addresses a different topic than the question, score = 1.

## Step 2: Required Elements
Identify the minimal information elements directly requested by the question, tailored for a lay audience.
- Extract solely from the question.
- Maintain the smallest necessary set.
- Break down multi-part questions into individual elements.
- Exclude clinical details not explicitly requested.

Exceptions:
- Document-check questions: "document does not specify" is valid.
- Referrals: Only count if the answer explicitly states lack of information and offers next steps.

## Step 3: Presence Check
Evaluate each required element as present or absent.
- Completeness: All elements present = COMPLETE
- Partial: Some elements present = PARTIAL
- Insufficient: None present = INSUFFICIENT

## Step 4: Distraction
Set high_distraction = true if unrelated content significantly distracts or obscures the answer; otherwise, false.

## Step 5: Additional Context
If completeness < COMPLETE, additional_context_present = false.
If completeness = COMPLETE, set additional_context_present = true only if it provides useful context for the specific question. Default: false when uncertain.

## Step 6: Final Score
Apply the first matching condition:
- 1: Step 1 failed
- 2: completeness = INSUFFICIENT
- 3: completeness = PARTIAL
- 4: completeness = COMPLETE AND (high_distraction = true OR additional_context_present = false)
- 5: completeness = COMPLETE AND high_distraction = false AND additional_context_present = true

## Output Format (strict)
Return valid JSON only. Do not output markdown.
Valid scores: 1–5 (1 = failed, 2 = insufficient, 3 = partial, 4 = complete but suboptimal, 5 = complete and optimal).

```json
{
  "reasoning": "Elements X, Y, Z were present. Score is 4 due to slight distraction.",
  "final_score": 4
}
```
