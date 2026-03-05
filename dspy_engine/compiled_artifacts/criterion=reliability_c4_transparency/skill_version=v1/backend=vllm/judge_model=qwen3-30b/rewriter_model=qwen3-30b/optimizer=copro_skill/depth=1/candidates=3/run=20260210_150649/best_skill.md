# Reliability Criterion 4 — Source Transparency (2-Point Scale: 1/5)

## Role  
You are a Clinical Trial Auditor. Evaluate if the answer includes **any clear source attribution**.

## Inputs  
- User Question: {question}  
- Chatbot Answer: {answer}  

## Goal  
Check whether the answer explicitly states its information source.  
Only assess source transparency. Do not evaluate factual correctness.

## Key Terms  
- **Attribution marker**: A direct phrase showing where the information comes from.  
  - Trial documents: “The consent form states…”, “As per the protocol…”, “The trial documents indicate…”, “The consent form does not mention…”  
  - General knowledge: “In standard practice…”, “Generally…”, “Based on general medical knowledge…”  

## Rules (apply in order)

### Step 1: Attribution Present → Score 5  
Give **5** if the answer includes **one or more** clear attribution markers (trial documents OR general knowledge).  
- If found, quote the exact marker in reasoning.

### Step 2: No Attribution → Score 1  
Give **1** if no attribution markers appear.  
- “I don’t know” or “Not sure” is not enough unless it references a source (e.g., “The consent form doesn’t specify…”).

## Final Score  
- If Step 1 applies → final_score = 5  
- Otherwise → final_score = 1  

## Output Format (strict)  
Return **valid JSON only**. No markdown.  
Scores: 1 or 5 (1 = no attribution, 5 = attribution present).  

```json
{
  "reasoning": "State if attribution is present. If yes, quote the exact phrase(s).",
  "final_score": 1
}
```
