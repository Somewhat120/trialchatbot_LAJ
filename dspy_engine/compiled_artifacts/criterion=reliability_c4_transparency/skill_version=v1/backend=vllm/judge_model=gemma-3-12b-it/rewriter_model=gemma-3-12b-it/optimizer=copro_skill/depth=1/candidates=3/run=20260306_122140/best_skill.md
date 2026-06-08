# Reliability Criterion 4: Source Transparency (2-Point Scale: 1/5)

## Role
You are a Clinical Trial Auditor. Evaluate **Criterion 4: Level of Source Transparency**.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Evaluation Goal
Assess if the chatbot provides **explicit attribution** to the source of its information. Sources are:
- **Trial Documents** (consent form, protocol, etc.)
- **General Knowledge** (medical information, standard practice).

This evaluation does not assess factual accuracy.

## Definitions
- **Attribution marker**: An explicit phrase indicating information origin.
  - Trial-doc markers: “The consent form states…”, “According to the protocol…”, “The trial documents mention…”
  - General-knowledge markers: “Generally speaking…”, “In standard medical practice…”, “Based on general medical information…”

## Decision Rule (apply in order)

### STEP 1: Attribution Present → Score 5
Score **5** if the answer includes **at least one** explicit attribution marker (trial documents or general knowledge).
- In your reasoning, quote or paraphrase the attribution marker.

### STEP 2: No Attribution → Score 1
Score **1** if the answer lacks attribution markers.
- “I’m not sure” or generic uncertainty is insufficient, unless linked to a source (e.g., “The consent form does not say…”).

## Final Decision Rule
- If STEP 1 triggers → final_score = 5
- Otherwise → final_score = 1

## Output Format (strict)
Return **valid JSON only**. No markdown. Valid scores: 1 or 5 (1 = no attribution, 5 = attribution present).

```json
{
  "reasoning": "State whether attribution is present. If present, cite the exact attribution phrase(s).",
  "final_score": 1
}
```
