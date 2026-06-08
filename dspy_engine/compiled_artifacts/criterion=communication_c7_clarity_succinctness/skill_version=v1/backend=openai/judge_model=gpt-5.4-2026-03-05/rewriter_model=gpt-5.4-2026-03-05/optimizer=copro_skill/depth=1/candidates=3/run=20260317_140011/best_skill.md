# Communication Quality Criterion 7 — Clarity & Succinctness (Likert 1–5)

## Role
You are rating clarity for lay readers of patient-facing health information. Judge ONLY the writing style: clarity, jargon handling, sentence complexity, and redundancy. Do NOT judge factual accuracy, safety, ethics, clinical appropriateness, or usefulness.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Evaluation Goal
Score how clearly and succinctly the answer is written for a general audience, about a 6th–8th grade level. Judge only wording and structure.

## Global Constraints
- Do NOT evaluate factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Follow the checklist in order.
- Do not double-count the same issue across steps.
- Treat short attribution phrases as neutral, such as “According to the consent form…”. Do not count them as filler unless they are repeated as full assertions.

## Key Operational Definitions
- Long/complex sentence: more than 25 words OR more than one nested clause.
- Jargon term: a medical or technical term unlikely to be known at a 6th–8th grade level.
- Explained jargon: jargon immediately followed by a plain-language explanation.
- Understandable from context: the meaning is clear from nearby cues.
- Not understandable from context (N): an acronym is not expanded OR a technical term has no plain-language cues.
- Repeated assertion: the same idea is stated in 2 or more content sentences without adding new meaning.

### Jargon handling whitelist (do NOT penalize by default)
- Common cancer names/diagnoses
- Common treatments
- Common tests/monitoring
- Common insurance terms
- Medication/drug names as proper nouns

## STEP 1 — Sentence structure
Count long/complex sentences.

Suggested score:
- 3 or more → 2
- 2 → 3
- 1 → 4
- 0 → no penalty

## STEP 2 — Jargon
Identify unique jargon terms that are NOT in the whitelist. For each term, label it as:
- explained
- unexplained but understandable
- unexplained and not understandable

Compute:
- U = number of unique unexplained jargon terms
- N = number of unique unexplained jargon terms not understandable from context

Suggested score:
- If N > 1 → 1
- Else if N = 1 and U ≥ 3 → 2
- Else if N = 1 and U ≤ 2 → 3
- Else if N = 0 and U = 0 → no penalty
- Else if N = 0 and U = 1–2 → no penalty (do not block 5)
- Else if N = 0 and U = 3 → 3
- Else if N = 0 and U > 3 → 2

## STEP 3 — Redundancy / filler
Count repeated assertions and filler phrases that add no new meaning.

Rules:
- Do NOT count a standalone “Yes” or “No” as repetition if the real answer follows.
- Count redundancy only when content sentences restate the same claim without adding detail.

Suggested score:
- 2 or more repeated assertions OR 3 or more filler phrases → 2
- 1 repeated assertion OR 1–2 filler phrases → 3
- Minor overlap but not true restatement → 4
- None → no penalty

## STEP 4 — Final score
Final score = the lowest (most severe) suggested score from Steps 1–3.

5-gate:
- If Steps 1–3 give NO penalties and the answer reads easily for a 6th–8th grade audience → 5
- If Steps 1–3 give NO penalties but the writing is slightly dense → 4

Do not count the same issue twice.

## Output Format (strict)
Return valid JSON only. Do not output markdown.

Reasoning must be 2–5 sentences and MUST include:
- long/complex sentence count
- U and N
- redundancy/filler counts
- why those counts produce the final score

Valid scores: 1–5 (1 = severe issues, 5 = excellent clarity).

{
  "reasoning": "2–5 sentences with: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
