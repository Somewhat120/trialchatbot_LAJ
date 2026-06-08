# Communication Quality Criterion 7 — Clarity & Succinctness (Likert 1–5)

## Role and Goal
Judge the writing style of {answer} for clarity and succinctness, considering a general audience (~6th–8th grade). Evaluate only how the answer is written, not its factual accuracy, safety, ethics, clinical appropriateness, or usefulness.

## Inputs and Constraints
- User Question: {question}
- Chatbot Answer: {answer}
- Do not evaluate factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Use a linear checklist to avoid double-counting issues.
- Brief attribution phrases are neutral unless repeated as full assertions.

## Definitions
- Long/complex sentence: >25 words or more than one nested clause.
- Jargon term: medical/technical term unlikely known at ~6th–8th grade.
- Explained jargon: immediately followed by a plain-language explanation.
- Understandable from context: meaning inferred from nearby cues.
- Not understandable from context (N): acronym not expanded or technical term without plain-language cues.
- Repeated assertion: same idea restated in 2+ content sentences without new meaning.

## Jargon Handling
Whitelisted terms (do not penalize by default): common cancer names/diagnoses, treatments, tests/monitoring, insurance terms, and medication/drug names as proper nouns.

## Evaluation Steps
### STEP 1 — Sentence Structure
Count long/complex sentences. Suggested scores:
- ≥3: 2
- 2: 3
- 1: 4
- 0: no penalty

### STEP 2 — Jargon
Identify unique jargon terms not in the whitelist. Compute:
- U = unexplained jargon terms
- N = unexplained jargon terms not understandable from context
Suggested scores:
- N > 1: 1
- N = 1 and U ≥ 3: 2
- N = 1 and U ≤ 2: 3
- N = 0 and U = 0: no penalty
- N = 0 and U = 1–2: no penalty
- N = 0 and U = 3: 3
- N = 0 and U > 3: 2

### STEP 3 — Redundancy/Filler
Count repeated assertions and filler phrases. Rules:
- Do not count standalone “Yes/No” as repetition.
- Count redundancy only when content sentences restate the same claim without adding detail.
Suggested scores:
- ≥2 repeated assertions or ≥3 filler phrases: 2
- 1 repeated assertion or 1–2 filler phrases: 3
- Minor overlap: 4
- None: no penalty

### STEP 4 — Final Score
Final score = the lowest suggested score from Steps 1–3. For a score of 5, Steps 1–3 must produce no penalties and the text must read easily for ~6th–8th grade.

## Output Format
Return valid JSON only.
```json
{
  "reasoning": "Include long/complex count, U and N, redundancy/filler counts, and final score justification.",
  "final_score": 3
}
```
