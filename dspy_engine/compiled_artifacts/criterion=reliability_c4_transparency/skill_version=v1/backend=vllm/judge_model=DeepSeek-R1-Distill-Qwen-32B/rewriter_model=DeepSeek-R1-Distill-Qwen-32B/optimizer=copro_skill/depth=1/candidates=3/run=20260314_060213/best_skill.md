Alright, I'm trying to rewrite the evaluation rubric for the LLM-as-Judge, specifically focusing on Criterion 4 about source transparency. The main goal is to improve clarity, ordering, and emphasize constraints without changing the original decision logic. 

First, I'll read through the original rubric to understand its structure and content. The rubric is divided into sections: Role, Inputs, Evaluation Goal, Definitions, Decision Rule, Final Decision Rule, and Output Format. Each section has specific instructions and criteria.

I notice that the original text is quite detailed and uses some technical terms. My task is to make this clearer and more concise. I should avoid redundancy and ensure that the meaning remains intact. Also, I need to keep placeholders like {question} and {answer} exactly as they are.

Starting with the Role section, it's clear but could be more concise. Instead of saying "You are an expert Clinical Trial Auditor," I could simplify it to "You are an expert auditor." This makes it more straightforward without losing the essence.

Next, the Inputs section is straightforward, so I might not need to change much there. However, I can make it more direct by saying "Your inputs are: {question} and {answer}."

Moving on to the Evaluation Goal, the original text mentions determining if the chatbot provides any explicit attribution. I can rephrase this to make it more direct, perhaps by saying "Determine if the chatbot provides any explicit attribution..." and then list the sources it should indicate.

In the Definitions section, the term "Attribution marker" is defined with examples. I can make this more concise by combining the examples and using bullet points for clarity. This helps in making the information more digestible.

Looking at the Decision Rule, it's divided into two steps. I need to ensure that the order is clear and the conditions are precisely stated. For Step 1, if there's any attribution marker, score 5. For Step 2, if no markers are present, score 1. I should make sure that the conditions are clearly stated without any ambiguity.

The Final Decision Rule is straightforward, so I might just need to rephrase it for clarity. The Output Format section needs to stay the same, ensuring that the JSON structure is preserved.

Throughout this process, I must ensure that I'm not adding any new rules or removing existing ones. The score scale remains unchanged, and the headings can be compressed if needed. I also need to keep the entire rewritten text under 2048 tokens, which shouldn't be a problem as I'm just rephrasing.

I should also avoid using any examples that change the scope and ensure that the language is plain and the sentences are short. This will help in improving clarity and reducing redundancy.

Finally, I'll review the rewritten rubric to ensure that it's meaningfully different in wording and structure from other candidates, as per the uniqueness requirement. I'll make sure that all placeholders are preserved and that the JSON output remains valid.

After making these changes, I'll double-check to ensure that the rubric's meaning and decision rules are preserved exactly as in the original. This way, the evaluation remains accurate and reliable without any unintended changes.
</think>

# Reliability Criterion 4 — Source Transparency (2-Point Scale: 1/5)

## Role
You are an expert auditor evaluating **Criterion 4: Source Transparency**.

## Inputs
- User Question: {question}
- Chatbot Answer: {answer}

## Evaluation Goal
Determine if the chatbot provides any explicit attribution showing whether its information comes from:
- Trial documents (consent form, protocol, etc.)
- General medical knowledge or standard practice

Do not assess factual accuracy.

## Definitions
- **Attribution marker**: Any explicit phrase indicating the source of information.
  - Trial-doc markers: "The consent form states...", "According to the protocol...", etc.
  - General-knowledge markers: "Generally speaking...", "In standard medical practice...", etc.

## Decision Rule (apply in order)

### STEP 1: Attribution Present → Score 5
Give **5** if the answer includes **any** explicit attribution marker (trial docs or general knowledge).
- If present, include the exact phrase(s) in your reasoning.

### STEP 2: No Attribution → Score 1
Give **1** if the answer has **no** attribution markers.
- Phrases like "I’m not sure" alone do NOT count unless they reference a source (e.g., "The consent form does not specify...").

## Final Decision Rule
- If STEP 1 applies → final_score = 5
- Otherwise → final_score = 1

## Output Format (strict)
Return valid JSON only. Scores: 1 or 5 (1 = no attribution, 5 = attribution present).

```json
{
  "reasoning": "State whether attribution is present. If present, cite the exact phrase(s).",
  "final_score": 1
}
```
