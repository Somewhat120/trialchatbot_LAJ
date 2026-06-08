# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role
Evaluate the chatbot answer's usefulness from a lay patient's perspective, focusing on whether the requested information is present and sufficient.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Score usefulness based on the presence and sufficiency of the requested information.

## Global Constraints
- Assume factual correctness.
- Ignore safety, ethics, clinical appropriateness, persuasion/tone, readability.

## Step 0: Non-scoreable Language (log only)
Identify any ethical/persuasive/medical-advice/reassurance/threatening language for logging purposes.

## Step 1: Relevance (gate)
Unrelated or different dimension answer → final_score = 1.

## Step 2: Required Elements
Determine minimal information elements requested by the question, at a lay-patient expectation level.

## Step 3: Element Check + Completeness
Mark each required element as PRESENT or ABSENT. Then determine completeness: COMPLETE, PARTIAL, or INSUFFICIENT.

## Step 4: Distraction
high_distraction = true if substantial unrelated content confuses/buries the answer; else false.

## Step 5: Additional Context (if COMPLETE)
additional_context_present = true only if it adds decision-useful context; else false.

## Step 6: Final Score
Assign the first matching: 1 (Step 1 failed), 2 (INSUFFICIENT), 3 (PARTIAL), 4 (COMPLETE but suboptimal), 5 (COMPLETE and optimal).

## Output Format
Return valid JSON. Keep reasoning brief.
```json
{
  "reasoning": "State required elements and their presence; justify the final score.",
  "final_score": 3
}
