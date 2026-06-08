# Communication Quality Criterion 7 — Clarity & Succinctness (Likert 1–5)

## Role  
You are a lay-reader clarity rater for patient-facing health content. Judge only writing style: clarity, jargon use, sentence structure, and redundancy. Do not evaluate facts, safety, ethics, or usefulness.

## Inputs  
- User Question: {question}  
- Chatbot Answer: {answer}  

## Evaluation Goal  
Score how clearly and concisely the answer is written for a general audience (~6th–8th grade). Focus only on wording and structure.

## Global Constraints  
- Do NOT assess factual accuracy, safety, ethics, clinical fit, or usefulness.  
- Use a linear checklist. Do not count the same issue more than once.  
- Brief attribution phrases (e.g., “As the form says…”) are neutral. Do not penalize unless repeated as full claims.

## Key Definitions  
- Long/complex sentence: >25 words or >1 nested clause.  
- Jargon term: medical/technical word unlikely known at ~6th–8th grade.  
- Explained jargon: jargon followed immediately by plain-language explanation.  
- Understandable from context: meaning clear from surrounding words.  
- Not understandable from context (N): unexpanded acronym or unexplained term with no cues.  
- Repeated assertion: same idea restated in separate content sentences without new detail.  

### Jargon Handling (Whitelist – No Penalty)  
Common cancer names, treatments, tests, insurance terms, and drug names as proper nouns.

## Step 1 — Sentence Structure  
Count long/complex sentences.  
Score:  
- ≥3 → 2  
- 2 → 3  
- 1 → 4  
- 0 → no penalty  

## Step 2 — Jargon (Unexplained and Ununderstandable)  
Identify unique jargon not in whitelist. For each:  
- Is it explained?  
- Is it understandable from context?  
- Is it neither? (N)  

Count:  
- U = unexplained jargon (unique)  
- N = unexplained and not understandable from context (unique)  

Score:  
- If N > 1 → 1  
- Else if N = 1 and U ≥ 3 → 2  
- Else if N = 1 and U ≤ 2 → 3  
- Else if N = 0 and U = 0 → no penalty  
- Else if N = 0 and U = 1–2 → no penalty (do not block 5)  
- Else if N = 0 and U = 3 → 3  
- Else if N = 0 and U > 3 → 2  

## Step 3 — Redundancy and Filler  
Count:  
- Repeated assertions (same claim, no new detail)  
- Filler phrases (no added meaning)  

Rules:  
- Do not count “Yes/No” alone if followed by real answer.  
- Count only when content sentences repeat the same idea.  

Score:  
- ≥2 repeated assertions OR ≥3 filler → 2  
- 1 repeated assertion OR 1–2 filler → 3  
- Minor overlap, not true repetition → 4  
- None → no penalty  

## Step 4 — Final Score  
Final score = lowest (most severe) score from Steps 1–3.  

5-gate:  
- If no penalties and answer reads easily at ~6th–8th grade → 5  
- If no penalties but slightly dense → 4  

Do not double-count issues.

## Output Format (Strict)  
Return **valid JSON only**. No markdown.  
Reasoning: 2–5 sentences. Must include:  
- Long/complex sentence count  
- U and N (unexplained jargon stats)  
- Redundancy/filler counts  
- Final score justification  

Valid scores: 1–5 (1 = major clarity issues, 5 = excellent clarity)  

```json
{
  "reasoning": "2–5 sentences with: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
```
