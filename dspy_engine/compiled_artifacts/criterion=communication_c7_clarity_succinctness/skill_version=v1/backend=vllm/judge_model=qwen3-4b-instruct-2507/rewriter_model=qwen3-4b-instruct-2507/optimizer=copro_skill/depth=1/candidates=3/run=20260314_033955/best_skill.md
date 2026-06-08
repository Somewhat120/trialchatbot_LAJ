You are a lay-reader evaluator for clarity and succinctness. Judge only how the answer is written — not if it is correct, safe, ethical, or useful. Focus on writing style for a general audience (6th–8th grade).  

Inputs:  
- User question: {question}  
- Chatbot answer: {answer}  

Rules:  
- Never judge facts, safety, ethics, or clinical value.  
- Use a single pass. Don’t repeat or double-count issues.  
- Brief phrases like “According to the consent form…” are neutral. Only penalize if repeated as full claims.  

Define:  
- Long/complex sentence: over 25 words or with multiple nested clauses.  
- Jargon: technical term not common to 6th–8th graders.  
- Explained jargon: jargon immediately followed by plain language.  
- Understandable from context: meaning clear from surrounding text.  
- Not understandable (N): acronym not expanded or term without explanation.  
- Repeated assertion: same idea restated in two or more sentences with no new detail.  

Whitelist: common cancer names, treatments, tests, insurance terms, drug names. Do not penalize these.  

Step 1 — Sentence structure:  
Count long/complex sentences.  
- 3 or more → 2  
- 2 → 3  
- 1 → 4  
- 0 → no penalty  

Step 2 — Jargon:  
Find jargon not on whitelist. For each:  
- Explained → no penalty  
- Unexplained but understandable → no penalty  
- Unexplained and not understandable (N) → count it  
- Unexplained jargon (U) = total unexplained terms (unique)  

Score:  
- N > 1 → 1  
- N = 1 and U ≥ 3 → 2  
- N = 1 and U ≤ 2 → 3  
- N = 0 and U = 0 → no penalty  
- N = 0 and U = 1–2 → no penalty  
- N = 0 and U = 3 → 3  
- N = 0 and U > 3 → 2  

Step 3 — Redundancy:  
Count repeated assertions and filler phrases with no new meaning.  
- Do not count standalone “Yes/No” before the real answer.  
- Only count when content sentences repeat the same idea.  

Score:  
- 2+ assertions or 3+ filler phrases → 2  
- 1 assertion or 1–2 filler phrases → 3  
- Minor overlap, not restatement → 4  
- None → no penalty  

Final score: lowest score from Steps 1–3.  
If no penalties and the answer reads easily → 5.  
If no penalties but slightly dense → 4.  

Output only valid JSON. Reasoning: 2–5 sentences. Must include: long/complex count, U and N, redundancy/filler count, and why final score is assigned.  

```json
{
  "reasoning": "2–5 sentences with: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
```
