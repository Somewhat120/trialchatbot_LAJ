# Reliability Criterion 5 — Factual Accuracy

## Role and Inputs
Evaluate the factual accuracy of a chatbot answer as an expert Informed Consent Form (ICF) auditor. 
- Source Text: {context}
- User Question: {question}
- Chatbot Answer: {answer}

## Task and Constraints
Determine the chatbot answer's factual accuracy. 
- Evaluate only verifiable factual content.
- Ignore usefulness, tone, persuasion, safety, or clarity.
- Use ICF text for TRIAL_SPECIFIC components and standard biomedical knowledge for GENERAL_KNOWLEDGE components.

## Classification and Scanning
### Step 0: Question Type
Classify the question as:
- TRIAL_SPECIFIC
- GENERAL_KNOWLEDGE
- MIXED

### Step 1: Early Major-Error Scan
Quickly scan for clearly fabricated or contradicted TRIAL_SPECIFIC facts.
- Count major errors (MAJOR_ERROR_COUNT).

## Misalignment and Content Removal
### Step 2: Misalignment Gate
Check if the answer provides generic content that doesn't address the requested component(s).
- Set MISALIGNMENT_PRESENT if true.

### Step 3: Remove Non-Scoreable Content
Extract verifiable factual content about trial procedures, eligibility, risks, benefits, etc.
- Ignore persuasion, reassurance, individualized advice, value judgments, etc.

## Component Evaluation
### Step 4: Explicitly Requested Components
List requested components and apply sufficiency rules:
- Eligibility: primary category satisfies the component.
- Timeline: concrete duration or follow-up window satisfies the component.
- Binary-ask: clearly answer yes or no.

## Assertion Labeling and Flagging
### Step 5: Extract Factual Assertions
Extract distinct factual assertions.
### Step 6: Label Each Assertion
Use severity order:
1. FABRICATION or FALSIFICATION
2. MISLEADING_INTERPRETATION
3. MISALIGNMENT
4. OMISSION
5. IMPRECISE
6. SUPPORTED

### Step 7: Compute Flags
Track:
- MAJOR_ERROR_COUNT
- CRITICAL_OMISSION_COUNT
- MISLEADING_PRESENT
- MISALIGNMENT_PRESENT
- IMPRECISION_PRESENT

## Final Score
Apply first match:
- Score 1: MAJOR_ERROR_COUNT ≥ 2 or CRITICAL_OMISSION_COUNT ≥ 2
- Score 2: MAJOR_ERROR_COUNT = 1 or CRITICAL_OMISSION_COUNT = 1
- Score 3: Noticeable deviation
- Score 4: Minor deviation
- Score 5: Fully accurate

## Output Format
Return valid JSON:
```json
{
  "reasoning": "Summarize question type, key labeled assertions, flags, and score trigger.",
  "final_score": 1
}
```
