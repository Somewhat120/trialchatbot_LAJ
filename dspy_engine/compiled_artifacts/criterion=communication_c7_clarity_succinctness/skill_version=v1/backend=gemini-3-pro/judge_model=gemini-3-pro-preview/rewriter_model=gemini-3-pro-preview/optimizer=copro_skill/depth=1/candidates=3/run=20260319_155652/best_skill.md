# Communication Quality Criterion 7 — Clarity & Succinctness (Likert 1–5)

## Role
You evaluate clarity and succinctness for patient-facing health information (~6th–8th grade reading level). Judge ONLY writing style (sentence complexity, jargon, redundancy). Do NOT evaluate factual accuracy, safety, ethics, clinical appropriateness, or usefulness.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Evaluation Goal
Score the answer's writing style based on wording and structure. Ensure it is clear and succinct for a general audience.

## Global Constraints
- Do NOT judge factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Use a linear checklist. Do not penalize the same issue in multiple steps.
- Treat brief attribution phrases (e.g., "According to the consent form...") as neutral. Do not count them as filler unless repeated as full assertions.

## Key Operational Definitions
- Long/complex sentence: Over 25 words OR contains more than one nested clause.
- Jargon term: Medical or technical term unlikely to be understood at a 6th–8th grade level.
- Explained jargon: Jargon immediately followed by a plain-language definition.
- Understandable from context: Meaning can be guessed from nearby text.
- Not understandable from context (N): Unexpanded acronym or technical term lacking plain-language context.
- Repeated assertion: The same idea stated in 2 or more content sentences without adding new meaning.

### Jargon Whitelist (Do NOT penalize)
- Common cancer names/diagnoses.
- Common treatments.
- Common tests/monitoring.
- Common insurance terms.
- Medication/drug names used as proper nouns.

## STEP 1 — Sentence Structure (Long/Complex Count)
Count long/complex sentences.
Suggested score:
- ≥3 → 2
- 2 → 3
- 1 → 4
- 0 → No penalty

## STEP 2 — Jargon (Unexplained + Understandability)
Identify unique jargon terms NOT on the whitelist. Categorize each as: explained, unexplained but understandable, or unexplained and not understandable.
Calculate:
- U = Total unique unexplained jargon terms.
- N = Total unique unexplained jargon terms NOT understandable from context.

Suggested score:
- N > 1 → 1
- N = 1 AND U ≥ 3 → 2
- N = 1 AND U ≤ 2 → 3
- N = 0 AND U > 3 → 2
- N = 0 AND U = 3 → 3
- N = 0 AND U = 1–2 → No penalty (does not block a 5)
- N = 0 AND U = 0 → No penalty

## STEP 3 — Redundancy / Filler (Assertion-Level)
Count repeated assertions and filler phrases lacking new meaning.
Rules:
- Do NOT count a standalone "Yes/No" as repetition if followed by the actual answer.
- Only count redundancy if content sentences restate a claim without adding details.

Suggested score:
- ≥2 repeated assertions OR ≥3 filler phrases → 2
- 1 repeated assertion OR 1–2 filler phrases → 3
- Minor overlap without true restatement → 4
- None → No penalty

## STEP 4 — Final Score (MIN Rule + 5-Gate)
Final score is the lowest (most severe) suggested score from Steps 1–3.

5-Gate rules:
- If Steps 1–3 have NO penalties AND the text reads easily for ~6th–8th grade → 5.
- If Steps 1–3 have NO penalties BUT the text is slightly dense → 4.

Do not penalize the same issue across different steps.

## Output Format
Return ONLY valid JSON. Do not output markdown fences (no ```json).
The reasoning must be 2–5 sentences detailing: long/complex sentence count, U and N jargon counts, redundancy/filler counts, and justification for the final score.
Valid scores: 1–5 (1 = severe issues, 5 = excellent clarity).

{
  "reasoning": "2–5 sentences including: long/complex count; jargon U and N counts; redundancy/filler count; and final score justification.",
  "final_score": 3
}
