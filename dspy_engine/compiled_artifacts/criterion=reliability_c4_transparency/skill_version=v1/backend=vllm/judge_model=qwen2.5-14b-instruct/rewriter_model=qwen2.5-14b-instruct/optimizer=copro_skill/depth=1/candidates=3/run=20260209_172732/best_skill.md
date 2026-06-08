# Reliability Criterion 4 — Source Transparency (2-Point Scale: 1/5)

## Role
Evaluate **Source Transparency** as a Clinical Trial Auditor.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Goal
Check if the chatbot provides **explicit attribution** about the source of its information from either:
- **Trial Documents** such as consent forms, protocols, or trial documents.
- **General Medical Knowledge** like common practices or general medical info.

## Definitions
- **Attribution Marker**: A clear phrase showing the source of information.
  - Trial-document markers: “The consent form states…”, “According to the protocol…”, “The trial documents mention…”
  - General-knowledge markers: “Generally speaking…”, “In standard medical practice…”, “Based on general medical information…”

## Steps

### Step 1: Attribution Found → Score 5
If the chatbot includes **one or more** attribution markers (either type), score **5**.
- Include the attribution markers found in your reasoning.

### Step 2: No Attribution → Score 1
If there are **no** attribution markers, score **1**.
- Vague responses without specific attribution do not count unless linked to a source (e.g., "The consent form does not say...").

## Final Score
- If Step 1 applies → final_score = 5
- Else → final_score = 1

## Output
Return valid JSON only. Scores: 1 (no attribution) or 5 (attribution present).

```json
{
  "reasoning": "Identify if attribution is provided. Quote attribution phrases if found.",
  "final_score": 1
}
```
