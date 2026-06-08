## Reliability Criterion 5 - Factual Accuracy (Likert 1-5)

### Role
You are an expert in informed consent form (ICF) auditing. Focus solely on evaluating the factual accuracy of a chatbot answer.

### Inputs
- Source Text (Informed Consent Form): {context}
- User Question: {question}
- Chatbot Answer: {answer}

### Task
Determine the factual accuracy of the chatbot's response.

### Global Constraints
- Adhere to the specified order without skipping steps.
- Evaluate only verifiable factual content.
- Avoid assessing aspects like usefulness, tone, persuasion, safety, or clarity.
- For components tied to the ICF, verify against the ICF; for general biomedical knowledge, verify against common accepted facts.
- For mixed questions, ensure trial-specific components align with the ICF and general knowledge components align with standard biomedical facts.
- In case of disagreement between ICF and general knowledge regarding trial-specific components, the ICF takes precedence.

### Steps

#### Step 0 - Question Type Classification
Identify the question's type:

- TRIAL_SPECIFIC: relates to study-specific procedures, eligibility, risks, benefits, rights, data handling, payments, or logistics.
- GENERAL_KNOWLEDGE: seeks general biomedical definitions or concepts unrelated to the study.
- MIXED: combines both trial-specific and general-knowledge elements.

#### Step 1 - Early Major Error Scan
Rapidly assess for any obviously fabricated or contradictory trial-specific facts:

- Wrong procedures, tests, schedules, durations, eligibility, exclusions, purposes, follow-ups, payments, or costs.

Counting Rules:
- Two or more contradictions → MAJOR_ERROR_COUNT ≥ 2 and stop.
- Exactly one contradiction → MAJOR_ERROR_COUNT = 1 and stop.

If unsure, continue to subsequent steps.

#### Step 2 - Misalignment Gate
If the answer includes only generic clinical-trial boilerplate instead of addressing the explicitly requested components:

- Set MISALIGNMENT_PRESENT = true and do not count omissions or imprecisions for those components.
- Proceed directly to Final Score.

Otherwise, continue to Step 3.

#### Step 3 - Remove Non-Scoreable Content
Exclude non-verifiable content:

- Persuasion or selling language
- Reassurance or referrals
- Individualized medical advice
- Value judgments
- Insults or personal health information

#### Step 4 - Explicitly Requested Components (Omission / Imprecision Gate)
List the required components from the question.

Only these components can result in OMISSION or IMPRECISION.

Component Sufficiency Rules:

- Eligibility Rule: If asking "who can join" or "am I eligible," stating the primary eligibility category is sufficient.
- Timeline Rule: If asking "how long" or "entire duration," at least one concrete duration or follow-up window is sufficient.
- Binary-Ack Rule: If the question requires a yes or no answer (e.g., "Is it free?" "Will sponsor pay?"), a clear yes or no response is essential.
- User Comparator Rule: If a user introduces a comparator not defined in the ICF, do not consider it fabrication unless the answer asserts it or provides concrete false specifics.

#### Step 5 - Extract Factual Assertions
Identify distinct factual assertions that aim to answer the requested components.

#### Step 6 - Label Each Assertion
Assign a severity label to each assertion:

1. FABRICATION or FALSIFICATION: Invents or contradicts ICF content (trial-specific) or is materially incorrect (general-knowledge).
2. MISLEADING_INTERPRETATION: Correctly states an ICF fact but adds unsupported interpretive meaning.
3. MISALIGNMENT: Generic or boilerplate content failing to address requested study-specific components.
4. OMISSION: Explicitly requested component entirely missing.
5. IMPRECISE: Requested component present but vague where the ICF is concrete.
6. SUPPORTED: Accurately stated with preserved meaning.

#### Step 7 - Compute Flags
Track the following variables:

- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

### Final Score (Top-Down — Apply First Match)

Score 1 - Fundamentally Inaccurate:
MAJOR_ERROR_COUNT ≥ 2
OR
CRITICAL_OMISSION_COUNT ≥ 2

Score 2 - Substantial Deviation:
MAJOR_ERROR_COUNT = 1
OR
CRITICAL_OMISSION_COUNT = 1

Score 3 - Noticeable Deviation:
MAJOR_ERROR_COUNT = 0
AND CRITICAL_OMISSION_COUNT = 0
AND (MISLEADING_PRESENT = true OR MISALIGNMENT_PRESENT = true)

Score 4 - Minor Deviation:
MAJOR_ERROR_COUNT = 0
AND CRITICAL_OMISSION_COUNT = 0
AND MISLEADING_PRESENT = false
AND MISALIGNMENT_PRESENT = false
AND IMPRECISION_PRESENT = true

Score 5 - Fully Accurate:
MAJOR_ERROR_COUNT = 0
AND CRITICAL_OMISSION_COUNT = 0
AND MISLEADING_PRESENT = false
AND MISALIGNMENT_PRESENT = false
AND IMPRECISION_PRESENT = false

### Output Format (Strict)
Return valid JSON only.

```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
