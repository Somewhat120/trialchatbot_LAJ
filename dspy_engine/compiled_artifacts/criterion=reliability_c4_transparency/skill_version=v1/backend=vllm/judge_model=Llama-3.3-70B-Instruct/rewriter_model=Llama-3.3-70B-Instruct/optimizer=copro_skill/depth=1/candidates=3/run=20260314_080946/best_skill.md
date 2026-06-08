# Reliability Criterion 4 — Level of Source Transparency (2-Point Scale: 1/5)

## Task
As a Clinical Trial Auditor, evaluate the chatbot's answer for **Criterion 4: Level of Source Transparency**.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Goal
Check if the chatbot provides explicit attribution for its information, citing either **Trial Documents** or **General Knowledge**.

## Key Concepts
- **Attribution marker**: A phrase signaling the information source.
  - Trial documents: “The consent form states…”, “According to the protocol…”
  - General knowledge: “Generally speaking…”, “In standard medical practice…”

## Evaluation Steps

1. **Attribution Present**: Score 5 if the answer contains at least one attribution marker.
   - Quote the marker in your reasoning.

2. **No Attribution**: Score 1 if there are no attribution markers.
   - Uncertainty phrases like “I’m not sure” don’t count unless tied to a source.

## Final Score
- If attribution is present, final_score = 5.
- Otherwise, final_score = 1.

## Output
Return valid JSON:
```json
{
  "reasoning": "State if attribution is present and cite the phrase if so.",
  "final_score": 1
}
```
