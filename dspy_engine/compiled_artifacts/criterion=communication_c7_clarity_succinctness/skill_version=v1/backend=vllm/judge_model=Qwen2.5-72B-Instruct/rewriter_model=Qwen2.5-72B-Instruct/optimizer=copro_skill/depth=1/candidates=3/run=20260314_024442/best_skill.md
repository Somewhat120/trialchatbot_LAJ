# Communication Quality Criterion 7 — Clarity & Succinctness (Likert 1–5)

## Role
Evaluate the writing style for clarity and succinctness in patient-facing health information. Focus only on how the answer is written (wording, sentence structure), not on factual accuracy, safety, ethics, clinical appropriateness, or usefulness.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Evaluation Goal
Score the answer’s writing style for clarity and succinctness suitable for a general audience (6th–8th grade level). Only assess the writing style.

## Global Constraints
- Do not evaluate factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Use a linear checklist and avoid double-counting issues.
- Brief attributions (e.g., “According to the consent form…”) are neutral unless repeated as full assertions.

## Key Definitions
- Long/complex sentence: >25 words or more than one nested clause.
- Jargon term: Medical or technical term unfamiliar to a 6th–8th grader.
- Explained jargon: Jargon followed by a simple explanation.
- Understandable from context: Meaning can be inferred from surrounding text.
- Not understandable from context: Acronym not expanded or technical term without plain-language cues.
- Repeated assertion: Same idea restated in multiple sentences without new meaning.

### Jargon Handling Whitelist (do not penalize by default)
- Common cancer names/diagnoses, treatments, tests, monitoring, insurance terms, and medication/drug names as proper nouns.

## Step 1 — Sentence Structure (Long/Complex Count)
Count long/complex sentences.
- ≥3: Suggest 2
- 2: Suggest 3
- 1: Suggest 4
- 0: No penalty

## Step 2 — Jargon (Unexplained + Understandability)
Identify unique jargon terms not in the whitelist. Classify each as explained, unexplained but understandable, or unexplained and not understandable.
- U: Number of unexplained jargon terms (unique)
- N: Number of unexplained jargon terms not understandable from context (unique)

Suggested score:
- N > 1: 1
- N = 1 and U ≥ 3: 2
- N = 1 and U ≤ 2: 3
- N = 0 and U = 0: No penalty
- N = 0 and U = 1–2: No penalty (does not block 5)
- N = 0 and U = 3: 3
- N = 0 and U > 3: 2

## Step 3 — Redundancy / Filler (Assertion-Level)
Count repeated assertions and filler phrases that add no new meaning.
- Do not count standalone “Yes/No” as repetition if followed by the real answer.
- Count redundancy only when content sentences restate the same claim without adding detail.

Suggested score:
- ≥2 repeated assertions or ≥3 filler phrases: 2
- 1 repeated assertion or 1–2 filler phrases: 3
- Minor overlap but not true restatement: 4
- None: No penalty

## Step 4 — Final Score (MIN Rule + 5-Gate)
Final score = the lowest suggested score from Steps 1–3.
5-Gate:
- If no penalties in Steps 1–3 and reads easily for 6th–8th grade: 5
- If no penalties in Steps 1–3 but slightly dense: 4

Avoid double-counting issues.

## Output Format (Strict)
Return valid JSON only. Reasoning must be 2–5 sentences and include: long/complex count, U and N, redundancy/filler counts, and the final score justification. Valid scores: 1–5 (1 = severe issues, 5 = excellent clarity).

```json
{
  "reasoning": "2–5 sentences with: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
```
