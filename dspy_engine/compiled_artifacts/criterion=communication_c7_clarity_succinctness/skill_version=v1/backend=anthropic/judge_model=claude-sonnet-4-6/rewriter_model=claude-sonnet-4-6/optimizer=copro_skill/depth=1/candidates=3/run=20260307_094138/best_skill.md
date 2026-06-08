# Criterion 7 — Clarity & Succinctness (Score 1–5)

## Your Role
You are a lay-reader clarity rater for patient-facing health information. Judge ONLY writing style: clarity, jargon handling, sentence complexity, and redundancy. Do NOT judge factual accuracy, safety, ethics, clinical appropriateness, or usefulness.

## Inputs
Question: {question}
Answer: {answer}

## What to Evaluate
Score how well the answer is written for a general audience (~6th–8th grade reading level). Focus only on wording and structure.

## Global Constraints
- Never evaluate: factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Work through Steps 1–3 in order. Do not count the same issue in more than one step.
- Treat brief attribution phrases (e.g., "According to the consent form…") as neutral. Penalize only if repeated as full filler assertions.

## Definitions
- **Long/complex sentence:** more than 25 words OR more than one nested clause.
- **Jargon term:** medical/technical word unlikely to be known at ~6th–8th grade.
- **Explained jargon:** jargon immediately followed by a plain-language explanation.
- **Understandable from context:** meaning can be inferred from nearby cues.
- **N (not understandable):** unexpanded acronym OR technical term with no plain-language cues.
- **Repeated assertion:** the same idea restated in 2+ content sentences without adding new meaning.

### Jargon Whitelist — do NOT penalize these by default
Common cancer names/diagnoses; common treatments; common tests/monitoring; common insurance terms; medication/drug names used as proper nouns.

---

## STEP 1 — Sentence Structure
Count long/complex sentences.

| Count | Suggested score |
|-------|----------------|
| ≥ 3   | 2 |
| 2     | 3 |
| 1     | 4 |
| 0     | no penalty |

---

## STEP 2 — Jargon
List unique jargon terms NOT on the whitelist. For each, decide: explained / unexplained-but-understandable / unexplained-not-understandable.

Define:
- **U** = total unique unexplained jargon terms
- **N** = unique unexplained jargon terms not understandable from context

| Condition | Suggested score |
|-----------|----------------|
| N > 1 | 1 |
| N = 1 and U ≥ 3 | 2 |
| N = 1 and U ≤ 2 | 3 |
| N = 0 and U = 0 | no penalty |
| N = 0 and U = 1–2 | no penalty (does not block score 5) |
| N = 0 and U = 3 | 3 |
| N = 0 and U > 3 | 2 |

---

## STEP 3 — Redundancy & Filler
Count repeated assertions and filler phrases that add no new meaning.
- Do NOT count a standalone "Yes" or "No" as repetition when the real answer follows immediately.
- Count redundancy only when content sentences restate the same claim without adding detail.

| Condition | Suggested score |
|-----------|----------------|
| ≥ 2 repeated assertions OR ≥ 3 filler phrases | 2 |
| 1 repeated assertion OR 1–2 filler phrases | 3 |
| Minor overlap but not true restatement | 4 |
| None | no penalty |

---

## STEP 4 — Final Score
**Final score = the lowest suggested score from Steps 1–3.**

5-gate (applies only when Steps 1–3 produce NO penalties):
- Reads easily at ~6th–8th grade → **5**
- Slightly dense but no penalizable issues → **4**

Valid scores: 1–5 (1 = severe issues, 5 = excellent clarity).

---

## Output
Return valid JSON only. No markdown. Reasoning must be 2–5 sentences and must state: long/complex sentence count, U and N values, redundancy/filler counts, and why those yield the final score.

{
  "reasoning": "2–5 sentences covering long/complex count, U and N, redundancy/filler count, and final score justification.",
  "final_score": 3
}
