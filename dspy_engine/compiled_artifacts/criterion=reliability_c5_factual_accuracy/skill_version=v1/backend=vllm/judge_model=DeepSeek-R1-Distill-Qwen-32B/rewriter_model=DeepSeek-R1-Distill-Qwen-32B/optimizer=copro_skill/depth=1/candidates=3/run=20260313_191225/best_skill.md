Okay, so I need to rewrite the evaluation rubric for the LLM-as-Judge focusing on Factual Accuracy. The goal is to make it clearer, better ordered, and emphasize constraints without changing the underlying logic. Let me go through each part step by step.

First, the role section says I'm an ICF auditor evaluating only factual accuracy. I should keep that concise. Maybe rephrase it to "Assess the factual accuracy of the chatbot's answer, focusing solely on verifiable information."

Looking at the inputs, they're placeholders, so I'll leave them as is. The task is straightforward, so no changes needed there.

Global constraints are a bit lengthy. I can condense them into bullet points for clarity, ensuring each constraint is clear and concise. For example, combining the points about using ICF and general knowledge into one bullet each.

Step 0 is about classifying the question type. I'll make sure to list the categories clearly without adding extra explanations.

In Step 1, the early error scan, I should keep the examples but make the counting rule more straightforward. Maybe rephrase it to avoid repetition and ensure it's clear when to stop.

Step 2 on misalignment needs to be concise. I'll make sure it's clear when to set the flag and what to do next.

Step 3 about removing non-scoreable content can be simplified by listing what to include and exclude in bullet points for better readability.

Step 4 deals with explicitly requested components. I'll restructure the rules into bullet points, making each rule clear and concise. For example, the eligibility rule can be a bullet point that states the condition without extra words.

Step 5 and 6 are about extracting assertions and labeling them. I'll ensure the labels are in order of severity and each is explained briefly.

Step 7 is about computing flags. I'll list each flag clearly, maybe as bullet points for better structure.

The final score section uses a top-down approach. I'll rephrase each score condition to be more direct, ensuring that the logic remains the same but the wording is clearer.

Finally, the output format needs to stay the same, so I'll just ensure it's concise without changing anything.

Throughout the rewrite, I'll aim for shorter sentences, remove redundancy, and make sure each section flows logically. I'll also check that all placeholders and JSON structure remain unchanged. The goal is to make the rubric as clear and concise as possible while preserving all the original rules and constraints.
</think>

# Reliability Criterion 5 — Factual Accuracy (Likert 1–5)

## Role
Assess the factual accuracy of the chatbot's answer, focusing solely on verifiable information.

## Inputs
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task
Determine if the chatbot's answer is factually accurate.

## Global Constraints
- Follow steps in order without skipping.
- Evaluate only verifiable facts.
- Exclude usefulness, tone, persuasion, safety, or clarity.
- Use ICF for TRIAL_SPECIFIC info.
- Use standard biomedical knowledge for GENERAL_KNOWLEDGE.
- For MIXED questions, verify TRIAL_SPECIFIC with ICF and GENERAL_KNOWLEDGE with standard info.
- ICF takes precedence in case of conflict.

## Step 0 — Question Type
Classify as:
- TRIAL_SPECIFIC: study-specific details.
- GENERAL_KNOWLEDGE: general biomedical concepts.
- MIXED: contains both.

## Step 1 — Major Error Check
Scan for obvious TRIAL_SPECIFIC errors like:
- Incorrect procedures, schedules, or exclusions.
- Wrong costs or study purpose.

Counting:
- ≥2 errors → MAJOR_ERROR_COUNT ≥2.
- Exactly 1 error → MAJOR_ERROR_COUNT=1.

Stop if confident, else proceed.

## Step 2 — Misalignment Check
If study-specific question and answer lacks direct address:
- Set MISALIGNMENT_PRESENT=true.
- Skip omission/imprecision for same components.
- Proceed to Final Score.

## Step 3 — Filter Non-Scoreable Content
Include only verifiable facts about:
- Procedures, eligibility, risks, benefits, rights, costs, data handling, or biomedical facts.

Exclude:
- Persuasion, reassurance, advice, judgments, or personal info.

## Step 4 — Key Components Check
List explicitly requested components.

Only these can cause OMISSION or IMPRECISION.

Rules:
- Eligibility: Primary category suffices unless qualifiers are key.
- Timeline: Vague where ICF is concrete → IMPRECISE. No timeframe → CRITICAL_OMISSION.
- Binary questions: Failing to answer yes/no → CRITICAL_OMISSION.
- Comparators: Treat as IMPRECISE unless ICF defines or provides false specifics.

## Step 5 — Extract Facts
List distinct factual claims addressing requested components.

## Step 6 — Label Claims
Use severity order:
1. FABRICATION/FALSIFICATION: ICF contradiction or wrong biomedical fact.
2. MISLEADING: Correct fact with unsupported meaning.
3. MISALIGNMENT: Boilerplate without addressing specifics.
4. OMISSION: Missing component.
5. IMPRECISE: Vague where concrete info exists.
6. SUPPORTED: Accurate and clear.

## Step 7 — Track Flags
- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

## Final Score
1. Fundamentally Inaccurate: MAJOR_ERROR_COUNT ≥2 or CRITICAL_OMISSION_COUNT ≥2.
2. Substantial Deviation: MAJOR_ERROR_COUNT=1 or CRITICAL_OMISSION_COUNT=1.
3. Noticeable Deviation: No major errors or omissions, but misleading or misaligned.
4. Minor Deviation: No major issues, but imprecise.
5. Fully Accurate: No issues detected.

## Output
```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
