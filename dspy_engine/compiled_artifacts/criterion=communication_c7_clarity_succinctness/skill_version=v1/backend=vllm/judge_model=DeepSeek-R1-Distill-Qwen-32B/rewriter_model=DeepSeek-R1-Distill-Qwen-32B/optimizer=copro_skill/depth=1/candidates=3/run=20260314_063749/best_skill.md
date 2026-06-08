Alright, I need to rewrite the evaluation rubric for the LLM-as-Judge, specifically the communication_c7_clarity_succinctness criterion. The goal is to improve clarity, ordering, and emphasize constraints without changing the underlying logic. Let me go through each part step by step.

First, I'll look at the headings. The original has a long title, so I'll shorten it to "Clarity & Succinctness (Likert 1–5)" to make it more concise. The role section mentions being a lay-reader, so I'll rephrase it to "You are a lay reader..." for brevity.

Next, the inputs are straightforward, so I'll keep them as-is, ensuring the placeholders {question} and {answer} remain unchanged. The evaluation goal needs to be more direct, so I'll change it to "Assess the answer’s writing style..." to focus on the task.

Looking at the global constraints, I'll combine the points to make them more succinct. Instead of listing them separately, I'll group related ideas, like not evaluating certain aspects and how to handle attribution phrases.

For the operational definitions, I'll simplify each term. For example, "Long/complex sentence" becomes "Sentence longer than 25 words..." to make it clearer. I'll also rephrase "Jargon term" to "Medical or technical term..." to avoid redundancy.

The whitelist section is important, so I'll rephrase it to "Common terms not penalized..." to make it more direct. I'll ensure the list remains the same but is presented more concisely.

In the steps, I'll streamline each section. For Step 1, I'll make the suggested scores more straightforward, using bullet points for clarity. Similarly, for Step 2, I'll rephrase the conditions to be more concise, using "If N > 1" and so on.

Step 3 on redundancy will be simplified by combining rules and making the suggested scores more direct. The final score section will be rephrased to "Determine the final score..." and clarify the 5-gate condition more succinctly.

I'll ensure the output format remains unchanged, keeping the JSON structure as required. The reasoning in the JSON should still include all necessary points but in a more concise manner.

Throughout the rewrite, I'll maintain the original meaning, avoid adding or removing rules, and ensure the text is clear and concise. I'll check that all placeholders are preserved and the JSON schema is intact. Finally, I'll ensure the rewritten rubric is within the token limit and flows logically from one section to the next.
</think>

# Clarity & Succinctness (Likert 1–5)

You are a lay reader assessing patient-facing health information. Evaluate ONLY WRITING STYLE: clarity, jargon, sentence structure, and redundancy. Do NOT assess accuracy, safety, ethics, or clinical appropriateness.

Inputs:
- User Question: {question}
- Chatbot Answer: {answer}

Goal: Assess the answer’s writing style for clarity and succinctness, targeting a general audience (~6th–8th grade reading level). Focus on how it is written, not what it says.

Constraints:
- Ignore factual accuracy, safety, ethics, clinical appropriateness, or usefulness.
- Use a linear checklist; do not double-count issues.
- Brief attribution phrases (e.g., “According to the consent form”) are neutral; only penalize if repeated as full assertions.

Definitions:
- Long/complex sentence: >25 words or multiple nested clauses.
- Jargon: Medical/technical term unlikely to be known by a 6th–8th grader.
- Explained jargon: Jargon immediately followed by plain-language explanation.
- Understandable from context: Meaning can be inferred from nearby cues.
- Not understandable from context (N): Acronym not expanded or technical term with no plain-language cues.
- Repeated assertion: Same idea restated in 2+ content sentences without new meaning.

Jargon whitelist (do NOT penalize):
- Common cancer names/diagnoses; common treatments; common tests/monitoring; common insurance terms; medication/drug names as proper nouns.

Step 1: Sentence Structure
Count long/complex sentences.
Score guide:
- ≥3 long/complex sentences → 2
- 2 → 3
- 1 → 4
- 0 → no penalty

Step 2: Jargon Handling
Identify unique jargon terms not in whitelist. For each, determine if explained, unexplained but understandable, or unexplained and not understandable.
Calculate:
- U = number of unexplained jargon terms (unique)
- N = number of unexplained jargon terms not understandable from context (unique)
Score guide:
- If N > 1 → 1
- Else if N = 1 and U ≥ 3 → 2
- Else if N = 1 and U ≤ 2 → 3
- Else if N = 0 and U = 0 → no penalty
- Else if N = 0 and U = 1–2 → no penalty (do not block 5)
- Else if N = 0 and U = 3 → 3
- Else if N = 0 and U > 3 → 2

Step 3: Redundancy/Filler
Count repeated assertions and filler phrases that add no new meaning.
Rules:
- Do NOT count standalone “Yes/No” as repetition when followed by the real answer.
- Only count redundancy when content sentences restate the same claim without adding detail.
Score guide:
- ≥2 repeated assertions OR ≥3 filler phrases → 2
- 1 repeated assertion OR 1–2 filler phrases → 3
- Minor overlap but not true restatement → 4
- None → no penalty

Step 4: Final Score
Final score = lowest (most severe) suggested score from Steps 1–3.
5-gate:
- If Steps 1–3 produced NO penalties AND reads easily for ~6th–8th grade → 5.
- If Steps 1–3 produced NO penalties but slightly dense → 4.
Do not count the same issue twice.

Output Format:
```json
{
  "reasoning": "Brief explanation (2–5 sentences) including: long/complex count; jargon U and N; redundancy/filler count; and final score justification.",
  "final_score": 3
}
```
