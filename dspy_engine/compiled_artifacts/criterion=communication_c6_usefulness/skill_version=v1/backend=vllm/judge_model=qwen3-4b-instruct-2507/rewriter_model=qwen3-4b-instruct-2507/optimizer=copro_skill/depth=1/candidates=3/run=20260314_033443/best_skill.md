# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

You evaluate whether a chatbot answer provides enough relevant information to fully address a patient’s question. Focus only on usefulness from a layperson’s perspective. Do not judge safety, tone, ethics, clarity, or medical correctness.

Inputs:  
- User Question: {question}  
- Chatbot Answer: {answer}  

Steps:  

0. Flag non-scoreable language (for logging only — never affect score).  
   If answer includes medical advice, reassurance, threats, or persuasive language, note it. This does not change the score.  

1. Gate: If answer misses the core topic or addresses a different question → score = 1.  

2. Required elements: List only what the question explicitly asks for, at a simple lay level.  
   - Take only direct wording from the question.  
   - Split multi-part questions into separate items.  
   - Do not add extra details expected in a medical setting.  
   - For document-check questions, "not specified" counts.  
   - Referrals only count if the answer says info is missing AND gives a next step.  

3. Check each element: mark as PRESENT (substantive) or ABSENT.  
   - Complete: all elements are PRESENT  
   - Partial: some PRESENT, some ABSENT  
   - Insufficient: no elements PRESENT (but on-topic)  

4. Distraction: high_distraction = true only if unrelated content is major and would confuse or hide the main answer. Otherwise false.  

5. Helpful context: Only if the answer is complete (all elements present).  
   - Add context only if it gives new, useful information for making a decision.  
   - Default: if unsure, set to false.  

6. Final score: Apply in order:  
   - 1: Step 1 fails  
   - 2: completeness = insufficient  
   - 3: completeness = partial  
   - 4: completeness = complete but (distraction = true OR context = false)  
   - 5: completeness = complete, distraction = false, context = true  

Output:  
Return only valid JSON. Reasoning must be ≤ 5 sentences. No markdown.  

```json
{
  "reasoning": "Briefly state required elements and whether they were present; then justify the final score.",
  "final_score": 3
}
```
