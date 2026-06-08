# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role  
Evaluate only usefulness for a lay patient with no medical background: whether the answer includes all needed information from the question.

## Inputs  
- User Question: {question}  
- Chatbot Answer: {answer}  

## Task  
Score usefulness based solely on presence and sufficiency of requested information.

## Global Constraints  
- Assume the answer is factually correct.  
- Do not assess: safety, ethics, clinical fit, tone, persuasion, or readability.  
- Follow steps strictly. Do not skip or reorder.

## Step 0: Flag non-scoreable language (log only)  
Note if the answer contains ethical, persuasive, medical advice, reassurance, or threatening language. This is for logging only. Do not let it affect scoring.

## Step 1: Relevance check (gate)  
If the answer does not address the question’s core topic or shifts focus → final_score = 1.

## Step 2: Extract required elements  
Identify the minimal set of information the question asks for, at a layperson’s level.  
- Base elements only on the question’s wording.  
- Include only what is explicitly requested.  
- Break multi-part questions into separate elements.  
- Do not add clinical expectations not in the question.  

Referral handling (Step 3 only):  
- “The document does not specify” counts as a response.  
- Referrals count only if the answer says information is unavailable AND gives a next step.

## Step 3: Check elements and completeness  
For each required element:  
- Mark as PRESENT (substantive) or ABSENT.  
Then determine completeness:  
- COMPLETE: all elements present  
- PARTIAL: some present, some absent  
- INSUFFICIENT: no elements present, but on topic

## Step 4: Distraction check  
Set high_distraction = true only if unrelated content is large and would obscure or confuse the answer. Otherwise, false.

## Step 5: Extra context (only if COMPLETE)  
If completeness ≠ COMPLETE → additional_context_present = false.  
If completeness = COMPLETE → additional_context_present = true only if the extra info adds useful, decision-relevant insight on the same topic.  
If unsure, default to false.

## Step 6: Final score (top-down)  
Assign first matching score:  
- 1: Step 1 failed  
- 2: completeness = INSUFFICIENT  
- 3: completeness = PARTIAL  
- 4: completeness = COMPLETE AND (high_distraction = true OR additional_context_present = false)  
- 5: completeness = COMPLETE AND high_distraction = false AND additional_context_present = true

## Output Format (strict)  
Return **valid JSON only**. No markdown. Reasoning must be ≤ 5 sentences.  
Valid scores: 1–5 (1 = failed, 2 = insufficient, 3 = partial, 4 = complete but suboptimal, 5 = complete and optimal).

```json
{
  "reasoning": "Briefly state required elements and whether they were present; then justify the final score.",
  "final_score": 3
}
```
