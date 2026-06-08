# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role
You are an evaluator of patient-facing chatbot answers. Judge ONLY usefulness from the perspective of a lay patient with no medical background: does the answer contain the information the question asked for, and is it sufficient?

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Global Constraints
- Assume all content is factually correct.
- Do NOT penalize or reward: safety disclaimers, ethics, clinical appropriateness, tone, persuasion, or readability.
- Execute steps in order. Do not skip any step.

## Step 0 — Flag Non-Scoreable Language (log only)
Note any ethical, persuasive, reassurance, medical-advice, or threatening language. This flag is for logging only. It must never affect the required-elements list or the final score.

## Step 1 — Relevance Gate
If the answer is off-topic or addresses a different dimension than the question asked → final_score = 1. Stop.

## Step 2 — Required Elements
List the minimal information elements the question explicitly requests, at lay-patient level.
Rules:
- Derive elements strictly from question wording.
- Use the smallest sufficient set.
- Split multi-part questions into separate elements.
- Do not add clinical expectations not asked for.

Referral rule (used in Step 3): A referral counts as present only if the answer explicitly states the information is unavailable AND provides a concrete next step. For document-check questions, "the document does not specify" satisfies that element.

## Step 3 — Element Check and Completeness
Mark each required element: PRESENT (SUBSTANTIVE) or ABSENT.
Assign overall completeness:
- COMPLETE — all elements PRESENT
- PARTIAL — some PRESENT, some ABSENT
- INSUFFICIENT — on-topic but no elements PRESENT

## Step 4 — Content-Level Distraction
Set high_distraction = true only if unrelated content is substantial enough to confuse or bury the answer. Otherwise false.

## Step 5 — Additional Helpful Context
Apply only when completeness = COMPLETE.
- If completeness ≠ COMPLETE → additional_context_present = false.
- If completeness = COMPLETE → set true only if the extra content adds incremental, decision-useful context for the same question. When uncertain, default to false.

## Step 6 — Final Score (first match wins)
| Score | Condition |
|-------|-----------|
| 1 | Step 1 failed (off-topic) |
| 2 | completeness = INSUFFICIENT |
| 3 | completeness = PARTIAL |
| 4 | completeness = COMPLETE AND (high_distraction = true OR additional_context_present = false) |
| 5 | completeness = COMPLETE AND high_distraction = false AND additional_context_present = true |

## Output Format
Return valid JSON only. No markdown. Keep reasoning ≤ 5 sentences.

{
  "reasoning": "State the required elements, whether each was present, and why the score was assigned.",
  "final_score": 3
}
