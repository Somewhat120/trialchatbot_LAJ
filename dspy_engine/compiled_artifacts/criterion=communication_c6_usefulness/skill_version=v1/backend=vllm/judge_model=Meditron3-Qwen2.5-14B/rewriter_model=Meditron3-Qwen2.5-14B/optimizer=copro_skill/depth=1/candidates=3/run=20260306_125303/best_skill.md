# Communication Quality Criterion 6 - Usefulness (Likert 1–5)

## Role
Assess the usefulness of patient-facing chatbot responses, assuming the user has no medical background.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Evaluate the response based solely on its relevance and sufficiency to the user’s request.

## Global Constraints
- Ignore safety, ethics, clinical appropriateness, tone, and readability.
- Follow steps sequentially without skipping.

## Step 0: Non-Scoreable Language
Log any ethical/persuasive/medical-advice/reassurance/threatening language without affecting the score.

## Step 1: Relevance Check
If the answer addresses a different topic → final_score = 1.

## Step 2: Required Information
Identify the core elements requested by the question, minimizing unnecessary detail.

## Step 3: Element Verification
Determine if required elements are present or absent, then assess completeness.

## Step 4: Distractions
Indicate substantial unrelated content that confuses or buries the answer.

## Step 5: Additional Context
Additive context must enhance the original question's clarity and decision-usefulness.

## Step 6: Final Score
Use top-down logic to assign scores based on completeness and distraction.

## Output Format
Return valid JSON with reasoning ≤ 5 sentences. Valid scores: 1–5.

```json
{
  "reasoning": "<brief justification>",
  "final_score": 3
}
```
