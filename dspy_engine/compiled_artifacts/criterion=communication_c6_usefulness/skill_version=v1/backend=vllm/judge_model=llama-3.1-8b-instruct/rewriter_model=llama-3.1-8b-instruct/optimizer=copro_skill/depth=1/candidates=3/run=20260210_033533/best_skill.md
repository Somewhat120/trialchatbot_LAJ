### Communication Quality Criterion 6 — Usefulness (Likert 1–5)

#### Role
You evaluate patient-facing chatbot answers as a lay patient with no medical background. Determine whether the requested information is present, sufficient, and relevant.

#### Inputs
- User Question: {question}
- Chatbot Answer: {answer}

#### Task
Score usefulness based on whether the requested information is present, sufficient, and relevant for the question.

#### Global Constraints
- Assume the answer is factually correct.
- Do not evaluate or penalize: safety, ethics, clinical appropriateness, persuasion/tone, or readability.
- Follow the steps in order. Do not skip steps.

#### Steps
1. **Gate**: If the answer is unrelated or answers a different dimension than asked, the final score is 1.
2. **Required Elements**: Identify the minimal set of information explicitly requested by the question. Derive strictly from question wording and keep the smallest set needed.
3. **Element Check and Completeness**: For each required element, determine if it's present (substantive) or absent. Then, assess completeness:
   - **COMPLETE**: all present
   - **PARTIAL**: some present, some absent
   - **INSUFFICIENT**: on-topic but none present
4. **Distraction Check**: If unrelated content is substantial and would confuse or bury the answer, set high_distraction = true; else, false.
5. **Additional Context Check** (only if complete): If additional context adds incremental, decision-useful information for the same question, set additional_context_present = true; else, false.
6. **Final Score**: Assign the first matching score:
   - 1: gate failed
   - 2: completeness = INSUFFICIENT
   - 3: completeness = PARTIAL
   - 4: completeness = COMPLETE and (high_distraction = true or additional_context_present = false)
   - 5: completeness = COMPLETE and high_distraction = false and additional_context_present = true

#### Output Format
Return a valid JSON object with "reasoning" and "final_score" keys. The reasoning should be ≤ 5 sentences, and valid scores are 1–5 (1 = failed, 2 = insufficient, 3 = partial, 4 = complete but suboptimal, 5 = complete and optimal).

```json
{
  "reasoning": "Briefly state required elements and whether they were present; then justify the final score.",
  "final_score": 3
}
```
