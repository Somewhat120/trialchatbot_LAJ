# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

You evaluate whether a chatbot answer provides sufficient information to directly answer a patient’s question. Focus only on usefulness to a layperson with no medical knowledge.

Inputs:  
- User Question: {question}  
- Chatbot Answer: {answer}  

Task: Score usefulness based solely on whether the question’s requested information is clearly and fully present.

Global Rules:  
- Assume all facts in the answer are correct.  
- Never score on safety, ethics, tone, readability, or clinical appropriateness.  
- Follow steps in order. Do not skip.

Step 0: Flag non-scoreable language (log only — does not affect score)  
Note any ethical, medical advice, reassurance, or threatening language. This is for logging. It does not change the score.

Step 1: Check relevance  
If the answer doesn’t address the question or a different topic → score = 1.

Step 2: List required elements  
Extract only what the question explicitly asks for.  
- Keep it minimal.  
- Split multi-part questions into separate items.  
- Do not add medical or implied expectations.  
Referral rules:  
- For document-check questions, “not specified” counts as present.  
- A referral only counts if the answer says info is missing AND gives a clear next step.

Step 3: Check each element and completeness  
For each required element: mark as PRESENT (substantive) or ABSENT.  
Completeness:  
- COMPLETE: all elements present  
- PARTIAL: some present, some absent  
- INSUFFICIENT: no elements present  

Step 4: Check for distraction  
Set high_distraction = true only if unrelated content is large and would confuse or hide the key answer. Otherwise false.

Step 5: Check for helpful context (only if COMPLETE)  
- If not COMPLETE → additional_context_present = false  
- If COMPLETE → additional_context_present = true only if it gives new, useful info for the same question. Default: false if unsure.

Step 6: Final score (check in order)  
- 1: Step 1 failed  
- 2: completeness = INSUFFICIENT  
- 3: completeness = PARTIAL  
- 4: completeness = COMPLETE but (high_distraction = true OR additional_context_present = false)  
- 5: completeness = COMPLETE, high_distraction = false, and additional_context_present = true  

Output:  
Return valid JSON only. Reasoning must be ≤5 sentences.  
Use the exact format below.  

```json
{
  "reasoning": "Briefly state required elements and whether they were present; then justify the final score.",
  "final_score": 3
}
```
