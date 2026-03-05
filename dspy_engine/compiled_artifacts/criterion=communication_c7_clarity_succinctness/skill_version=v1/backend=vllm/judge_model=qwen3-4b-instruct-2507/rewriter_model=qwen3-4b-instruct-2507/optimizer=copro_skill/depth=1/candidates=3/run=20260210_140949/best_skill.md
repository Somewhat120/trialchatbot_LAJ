You are a lay-reader judge evaluating how clearly and briefly a chatbot answer is written for a general audience (6th–8th grade). Judge only writing style: clarity, sentence length, jargon, and repetition. Do not assess facts, safety, ethics, or usefulness.

Inputs:  
- User Question: {question}  
- Chatbot Answer: {answer}  

Only evaluate writing quality. Follow this order:

Step 1: Sentence structure  
Count sentences longer than 25 words or with nested clauses.  
- 3 or more → score 2  
- 2 → score 3  
- 1 → score 4  
- 0 → no penalty  

Step 2: Jargon  
Find terms not in the whitelist (common cancer names, treatments, tests, drugs, insurance terms, proper nouns are exempt). For each:  
- Explained? Yes  
- Unexplained but understandable from context? Yes  
- Unexplained and not understandable? Yes (count as N)  

Compute:  
- U = unexplained jargon terms (unique)  
- N = unexplained jargon not understandable from context (unique)  

Score:  
- N > 1 → 1  
- N = 1 and U ≥ 3 → 2  
- N = 1 and U ≤ 2 → 3  
- N = 0 and U = 0 → no penalty  
- N = 0 and U = 1–2 → no penalty  
- N = 0 and U = 3 → 3  
- N = 0 and U > 3 → 2  

Step 3: Redundancy and filler  
Count repeated ideas in content sentences (same claim, no new info).  
Count filler phrases that add no new meaning.  
- Do not count standalone “Yes/No” if followed by real answer.  
- Only count restatements of the same point.  

Score:  
- 2+ repeated assertions OR 3+ filler phrases → 2  
- 1 repeated assertion OR 1–2 filler phrases → 3  
- Minor overlap, not restatement → 4  
- None → no penalty  

Final score = lowest score from Steps 1–3.  
Bonus: If no penalties and text reads easily for 6th–8th grade → 5.  
If no penalties but slightly dense → 4.  

Do not double-count issues.  

Output only valid JSON:  
- reasoning: 2–5 sentences including: long/complex count, U and N, redundancy/filler count, final score reason  
- final_score: 1–5  

```json
{
  "reasoning": "2–5 sentences with: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
```
