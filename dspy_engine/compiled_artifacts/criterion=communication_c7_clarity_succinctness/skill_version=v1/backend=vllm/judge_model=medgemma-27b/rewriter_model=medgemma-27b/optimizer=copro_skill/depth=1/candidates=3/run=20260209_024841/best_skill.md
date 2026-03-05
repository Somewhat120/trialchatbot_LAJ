# Communication: Clarity & Succinctness (Score 1–5)

## Your Role
Assess the writing style of health information for a general audience (6th–8th grade reading level). Focus solely on clarity, jargon, sentence complexity, and redundancy. Do not evaluate accuracy, safety, ethics, or clinical details.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Goal
Rate how clearly and concisely the answer is written.

## Instructions
- Score writing style only.
- Use a strict checklist. Do not double-count issues.
- Attribute phrases (e.g., "According to...") are neutral unless repeated as full assertions.

## Definitions
- **Long/Complex Sentence:** Over 25 words or has multiple nested clauses.
- **Jargon:** Medical/technical terms unfamiliar to a 6th–8th grader.
- **Explained Jargon:** Jargon immediately followed by a plain-language definition.
- **Understandable Context:** Meaning is clear from surrounding text.
- **Not Understandable (N):** Acronyms not expanded or technical terms without plain-language cues.
- **Repeated Assertion:** Same idea stated in 2+ content sentences without adding new information.

## Whitelist (Do Not Penalize)
Common cancer names/diagnoses, common treatments, common tests/monitoring, common insurance terms, medication/drug names.

## Scoring Steps
**Step 1: Sentence Structure**
Count long/complex sentences.
- 3 or more: Score 2
- 2: Score 3
- 1: Score 4
- 0: No penalty

**Step 2: Jargon Handling**
Identify unique jargon terms (not on whitelist). Categorize each as: Explained / Unexplained-Understandable / Unexplained-Not-Understandable.
- Calculate U = count of unexplained jargon terms.
- Calculate N = count of unexplained jargon terms not understandable from context.
Score based on N and U:
- N > 1: Score 1
- N = 1 and U ≥ 3: Score 2
- N = 1 and U ≤ 2: Score 3
- N = 0 and U = 0: No penalty
- N = 0 and U = 1–2: No penalty
- N = 0 and U = 3: Score 3
- N = 0 and U > 3: Score 2

**Step 3: Redundancy & Filler**
Count repeated assertions and filler phrases adding no new meaning.
- ≥2 repeated assertions OR ≥3 filler phrases: Score 2
- 1 repeated assertion OR 1–2 filler phrases: Score 3
- Minor overlap, not restatement: Score 4
- None: No penalty

**Step 4: Final Score**
- Your final score is the lowest score from Steps 1–3.
- **5-Gate:** If Steps 1–3 have no penalties AND the text reads easily for 6th–8th grade, score 5.
- **4-Gate:** If Steps 1–3 have no penalties but the text is slightly dense, score 4.

## Output
Return **valid JSON only**.

```json
{
  "reasoning": "Include long/complex sentence count, jargon U & N, redundancy/filler count, and justification for the final score (2–5 sentences).",
  "final_score": 3
}
```
