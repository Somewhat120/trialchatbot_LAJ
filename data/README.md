# Evaluation subset

`evaluation_subset.jsonl` contains 200 question-answer pairs with trial and category metadata and clinician reference scores across seven criteria. The full dataset will be released upon publication.

## Composition

- 100 records from the oncology dataset: 82 oncology trial-related questions and 18 general questions.
- 100 non-oncology records across 20 trials, with five per trial and 20 per disease category.
- Overall question types: 153 `TRIAL_SPECIFIC`, 29 `MIXED`, and 18 `GENERAL`.
- Annotation labels: 139 `dual_annotation` and 61 `single_annotation`. All 100 non-oncology records are dual-annotated.

| Non-oncology category | Records |
|---|---:|
| Neuropsychiatric | 20 |
| Cardiovascular | 20 |
| Infectious / Parasitic | 20 |
| Musculoskeletal | 20 |
| Digestive | 20 |

## Fields

1. `qa_id`
2. `trial_num`
3. `cancer_type`
4. `question_type`
5. `element`
6. `question`
7. `answer`
8. `Presence of critical harms`
9. `Presence of individualized medical advice`
10. `Presence of inappropriate persuasion`
11. `Level of source transparency`
12. `Level of factual accuracy`
13. `Level of usefulness`
14. `Level of clarity and succinctness`
15. `annotation`
16. `quality_tier`
17. `health_category`

`health_category` identifies the cancer or non-oncology disease category and is null for general questions. `cancer_type` is null for non-oncology records.

The seven score fields use integer values: C1-C3 are 0 or 1, source transparency (C4) uses the endpoints 1 or 5, and C5-C7 are 1 through 5.

`quality_tier` identifies answers manually edited to be "perfect" (`optimized`) and answers without this manual editing (`non_optimized`). A null value indicates that no tier is recorded.
