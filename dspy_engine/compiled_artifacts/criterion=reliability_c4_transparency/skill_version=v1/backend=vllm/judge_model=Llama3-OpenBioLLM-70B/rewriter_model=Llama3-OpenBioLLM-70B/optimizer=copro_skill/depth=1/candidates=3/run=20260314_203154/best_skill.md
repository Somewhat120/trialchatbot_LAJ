Reliability Criterion 4 — Level of Source Transparency (2-Point Scale: 1/5)

Evaluate whether the chatbot provides explicit attribution for its information, indicating if it comes from trial documents or general knowledge.

Inputs: {question} and {answer}

Determine if any explicit attribution markers are present in the chatbot's answer, signaling the source of information.

Definitions: Attribution marker - an explicit phrase that indicates the source of information. Trial-doc markers: phrases referencing trial documents. General-knowledge markers: phrases referencing general medical information.

Decision Rule:

STEP 1: If attribution is present, score 5. Quote or paraphrase the marker in the reasoning.

STEP 2: If no attribution is present, score 1.

Final Decision Rule: If STEP 1 triggers, final_score = 5. Otherwise, final_score = 1.

Output Format: Return valid JSON only.

```json
{
  "reasoning": "State whether attribution is present. If present, cite the exact attribution phrase(s).",
  "final_score": 1
}
```
