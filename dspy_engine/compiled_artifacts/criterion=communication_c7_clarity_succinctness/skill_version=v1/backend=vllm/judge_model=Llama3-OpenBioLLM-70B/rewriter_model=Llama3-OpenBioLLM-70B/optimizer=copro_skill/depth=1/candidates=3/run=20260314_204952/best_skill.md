# Communication Quality Criterion 7 — Clarity & Succinctness (Likert 1–5)

## Role
Evaluate the writing style of patient-facing health information as a lay-reader clarity rater, focusing on clarity, jargon handling, sentence complexity, and redundancy.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Evaluation Goal
Score the answer's writing style for clarity and succinctness for a general audience (~6th–8th grade).

## Global Constraints
- Ignore factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Use a linear checklist without double-counting issues.
- Treat brief attribution phrases as neutral unless repeated as full assertions.

## Key Operational Definitions
- Long/complex sentence: >25 words or multiple nested clauses.
- Jargon term: medical/technical term unlikely known at ~6th–8th grade.
- Explained jargon: jargon with immediate plain-language explanation.
- Understandable from context: meaning can be inferred from nearby cues.
- Not understandable from context (N): unexpanded acronym or technical term without cues.
- Repeated assertion: same idea restated in 2+ content sentences.

### Jargon handling whitelist
- Common cancer names/diagnoses, treatments, tests/monitoring, insurance terms, and medication/drug names as proper nouns.

## STEP 1 — Sentence structure
Count long/complex sentences.
Suggested score:
- ≥3 → 2
- 2 → 3
- 1 → 4
- 0 → no penalty

## STEP 2 — Jargon
Identify unique jargon terms not in the whitelist. For each, determine if explained or understandable from context.
Compute:
- U = number of unexplained jargon terms (unique)
- N = number of unexplained jargon terms not understandable from context (unique)

Suggested score:
- If N > 1 → 1
- Else if N = 1 and U ≥ 3 → 2
- Else if N = 1 and U ≤ 2 → 3
- Else if N = 0 and U = 0 → no penalty
- Else if N = 0 and U = 1–2 → no penalty
- Else if N = 0 and U = 3 → 3
- Else if N = 0 and U > 3 → 2

## STEP 3 — Redundancy / filler
Count repeated assertions and filler phrases adding no new meaning.
Rules:
- Ignore standalone “Yes/No” when followed by a real answer.
- Count redundancy only when content sentences restate the same claim without detail.
Suggested score:
- ≥2 repeated assertions OR ≥3 filler phrases → 2
- 1 repeated assertion OR 1–2 filler phrases → 3
- Minor overlap but not true restatement → 4
- None → no penalty

## STEP 4 — Final score
Final score = the lowest suggested score from Steps 1–3.
5-gate:
- If Steps 1–3 produced no penalties and reads easily for ~6th–8th grade → 5.
- If Steps 1–3 produced no penalties but slightly dense → 4.

Do not double-count issues.

## Output Format
Return valid JSON only. Reasoning must include long/complex count, U and N, redundancy/filler counts, and final score justification.
```json
{
  "reasoning": "2–5 sentences with: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
```
