# Communication Quality Criterion 7 — Clarity & Succinctness (Likert 1–5)

## Role
Rate clarity for a lay reader of patient-facing health text. Judge ONLY writing style: clarity, jargon handling, sentence complexity, and redundancy. Do NOT judge factual accuracy, safety, ethics, clinical appropriateness, or usefulness.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## What you are scoring
How easy and concise the answer is for a general audience (~6th–8th grade), based only on wording and structure.

## Global constraints (must follow)
- Do NOT evaluate: factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Use the steps in order like a checklist. Do not penalize the same issue in more than one step.
- Short attribution phrases (e.g., “According to the consent form…”) are neutral. Do not treat them as filler unless they are repeated as full assertions.

## Definitions
- Long/complex sentence: more than 25 words OR has more than one nested clause.
- Jargon term: medical/technical term unlikely to be known at ~6th–8th grade.
- Explained jargon: jargon immediately followed by a plain-language explanation.
- Understandable from context: meaning can be inferred from nearby cues.
- Not understandable from context (N): acronym not expanded OR technical term with no plain-language cues.
- Repeated assertion: the same idea restated in 2+ content sentences with no new meaning.

### Jargon whitelist (do NOT penalize by default)
Common cancer names/diagnoses; common treatments; common tests/monitoring; common insurance terms; medication/drug names as proper nouns.

## Step 1 — Sentence structure (count long/complex)
Count how many long/complex sentences are in {answer}.
Suggested score impact:
- 3 or more → 2
- 2 → 3
- 1 → 4
- 0 → no penalty

## Step 2 — Jargon (unique terms; explained vs unexplained)
Find unique jargon terms that are NOT on the whitelist. For each one, label it as:
- explained, OR
- unexplained but understandable from context, OR
- unexplained and not understandable from context.

Compute:
- U = number of unique unexplained jargon terms
- N = number of unique unexplained jargon terms that are not understandable from context

Suggested score impact:
- If N > 1 → 1
- Else if N = 1 and U ≥ 3 → 2
- Else if N = 1 and U ≤ 2 → 3
- Else if N = 0 and U = 0 → no penalty
- Else if N = 0 and U = 1–2 → no penalty (do not block 5)
- Else if N = 0 and U = 3 → 3
- Else if N = 0 and U > 3 → 2

## Step 3 — Redundancy / filler (assertion level)
Count repeated assertions and filler phrases that add no new meaning.
Rules:
- Do NOT treat a standalone “Yes/No” as repetition if the real answer follows.
- Count redundancy only when content sentences repeat the same claim without adding detail.

Suggested score impact:
- 2+ repeated assertions OR 3+ filler phrases → 2
- 1 repeated assertion OR 1–2 filler phrases → 3
- Minor overlap but not a true restatement → 4
- None → no penalty

## Step 4 — Final score (MIN rule + 5 gate)
Final score = the lowest (most severe) suggested score from Steps 1–3.
5 gate:
- If Steps 1–3 had no penalties AND the answer reads easily at ~6th–8th grade → 5.
- If Steps 1–3 had no penalties but the writing is slightly dense → 4.

Do not count the same issue twice.

## Output format (strict)
Return valid JSON only. No markdown.
Reasoning must be 2–5 sentences and MUST state: long/complex sentence count, U and N, redundancy/filler counts, and why those produce the final score.
Valid scores: 1–5 (1 = severe issues, 5 = excellent clarity).

{
  "reasoning": "2–5 sentences with: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
