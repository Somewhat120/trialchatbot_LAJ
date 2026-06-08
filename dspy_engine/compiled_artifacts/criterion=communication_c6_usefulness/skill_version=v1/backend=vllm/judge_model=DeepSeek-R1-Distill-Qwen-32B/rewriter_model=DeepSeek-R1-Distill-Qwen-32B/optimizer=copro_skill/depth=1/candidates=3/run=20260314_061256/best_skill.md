Okay, so I need to rewrite the evaluation rubric for the communication_c6_usefulness criterion. The goal is to improve clarity, ordering, and emphasize constraints without changing the underlying logic. Let me go through each part step by step.

First, the role section. It's about evaluating a chatbot's answer from a lay patient's perspective. I should make it more concise, maybe combine some sentences to make it clearer.

Next, the inputs are placeholders, so I'll keep them as they are. The task needs to be more straightforward, so I'll rephrase it to focus on the presence and sufficiency of the information.

Looking at the global constraints, I should ensure they're clearly listed without redundancy. Maybe bullet points would help, but since I can't use markdown, I'll just list them plainly.

Step 0 is about flagging non-scoreable language. I need to make sure it's clear that this doesn't affect the score, just logging. I'll rephrase it to be more direct.

Step 1 is a gate for relevance. If the answer is off-topic, the score is 1. I'll make this step more concise.

Step 2 involves listing required elements. I'll simplify the rules, maybe combine them into a single sentence each. Also, clarify referral handling without using sub-steps.

Step 3 is checking each element's presence and determining completeness. I'll rephrase this to make the categories (complete, partial, insufficient) more distinct.

Step 4 is about content-level distraction. I'll define high distraction more clearly, ensuring it's only when content is substantial and confusing.

Step 5 deals with additional context. I'll make it clear that this only applies if completeness is full and the context adds value.

Step 6 assigns the final score based on the first matching condition. I'll rephrase each condition to be more straightforward, ensuring the scoring logic remains the same.

The output format needs to stay the same, so I'll just restate it clearly, emphasizing valid JSON and concise reasoning.

Throughout, I'll avoid any markdown, keep sentences short, and ensure the rewritten text is within the token limit. I'll also make sure to keep placeholders like {question} and {answer} intact.

I think that covers all the parts. Now, I'll go through each section again to ensure clarity and that no constraints are altered. Making sure each step flows logically and the instructions are as clear as possible without redundancy.
</think>

# Communication Quality Criterion 6 — Usefulness (Likert 1–5)

## Role
You evaluate chatbot answers from a lay patient's perspective, assessing if the requested information is present and sufficient.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Score usefulness based solely on whether the requested information is present and sufficient.

## Global Constraints
- Assume the answer is factually correct.
- Do not evaluate safety, ethics, clinical appropriateness, persuasion/tone, or readability.
- Follow steps in order.

## Step 0: Identify Non-Scoreable Language
Check for ethical, persuasive, medical advice, reassurance, or threatening language. This is for logging only and does not affect the score.

## Step 1: Relevance Check
If the answer is unrelated or addresses a different dimension than the question, score = 1.

## Step 2: Determine Required Elements
Extract the minimal set of information explicitly requested by the question, at a lay-patient level. Split multi-part questions into separate elements. Do not add clinical expectations not requested.

Referral Handling:
- For document-check questions, "the document does not specify" counts.
- Referrals count only if the answer explicitly states info is unavailable and provides a next step.

## Step 3: Element Presence and Completeness
For each required element: mark as PRESENT (SUBSTANTIVE) or ABSENT. Then determine completeness:
- COMPLETE: all elements present
- PARTIAL: some present, some absent
- INSUFFICIENT: on-topic but no elements present

## Step 4: Content Distraction
high_distraction = true only if unrelated content is substantial and would confuse or bury the answer; else false.

## Step 5: Additional Context
If completeness != COMPLETE → additional_context_present = false.
If completeness = COMPLETE → additional_context_present = true only if it adds incremental, decision-useful context for the same question.
Default: if unsure, false.

## Step 6: Final Score
Assign the first matching:
- 1: Step 1 failed
- 2: completeness = INSUFFICIENT
- 3: completeness = PARTIAL
- 4: completeness = COMPLETE AND (high_distraction = true OR additional_context_present = false)
- 5: completeness = COMPLETE AND high_distraction = false AND additional_context_present = true

## Output Format
Return valid JSON only. Keep reasoning ≤ 5 sentences.

```json
{
  "reasoning": "Briefly state required elements and their presence; then justify the final score.",
  "final_score": 3
}
```
