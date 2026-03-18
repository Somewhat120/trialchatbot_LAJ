# Resubmit Requirements: Anthropic (dataset=test_set0226)

## Goal
Repair anthropic scoring outputs that currently contain `status=error`, `parse_error`, or `ai_score=-1` rows.

## Required Inputs

- Backend: `anthropic`
- Model: `claude-sonnet-4-6`
- Dataset: `test_set0226`
- Scope: all files under `LAJ_score_outputs/dataset=test_set0226/**/backend=anthropic/**`

## Rows To Repair

- Total problematic rows: `33`
- Rule for valid score:
  - `c1-c3`: allowed `ai_score` in `{0,1}`
  - `c4-c7`: allowed `ai_score` in `{1,2,3,4,5}`

## Resubmit Strategy

1. Re-run only failed rows (`status=error` OR `parse_error != null` OR `ai_score=-1`) for affected files.
2. Keep all existing valid rows unchanged.
3. Replace each failed row only when the retry returns a valid score per criterion range.
4. Maintain exactly one row per `qa_id` per file.
5. Keep file row count at `800` after repair.

## Acceptance Gates

A repaired file is accepted only if all are true:

- `parse_error == 0`
- `status=error == 0`
- `ai_score=-1 == 0`
- `rows == 800`
- Score range validity holds by criterion (`c1-c3` binary, `c4-c7` Likert 1-5)

## Post-Repair Validation

Run an audit equivalent to:

- missing required files check
- big error checks (`parse_error`, `status=error`, `rows<800`, `ai_score=-1`)
- criterion-specific score range validation

Only push when all affected files pass acceptance gates.
