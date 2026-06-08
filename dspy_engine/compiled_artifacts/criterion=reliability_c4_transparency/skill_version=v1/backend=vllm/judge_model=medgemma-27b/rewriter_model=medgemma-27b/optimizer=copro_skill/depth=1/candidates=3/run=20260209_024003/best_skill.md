# Reliability Criterion 4: Source Transparency (Score: 1 or 5)

## Role
Act as a Clinical Trial Auditor. You will assess the chatbot's source transparency.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Goal
Identify if the chatbot explicitly states the source of its information. Does it cite the **Trial Documents** (like protocol or consent form) or **General Knowledge**?

Do not evaluate factual accuracy.

## Definitions
- **Attribution Marker**: An explicit phrase indicating the information source.
  - Trial-doc examples: “The consent form states…”, “According to the protocol…”, “The trial documents mention…”
  - General-knowledge examples: “Generally speaking…”, “In standard medical practice…”, “Based on general medical information…”

## Decision Process

### Step 1: Attribution Found → Score 5
Assign a score of **5** if the answer includes **at least one** attribution marker.
- Your reasoning must quote or paraphrase the marker.

### Step 2: No Attribution Found → Score 1
Assign a score of **1** if the answer contains **zero** attribution markers.
- Generic uncertainty like "I'm not sure" does not count unless linked to a source (e.g., "The consent form does not say...").

## Final Score
- If Step 1 applies, final_score = 5.
- Otherwise, final_score = 1.

## Output Format
Return only valid JSON.

```json
{
  "reasoning": "State if attribution is present. If yes, quote the exact attribution phrase(s).",
  "final_score": 1
}
```
