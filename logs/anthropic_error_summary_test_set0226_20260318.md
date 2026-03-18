# Anthropic Output Error Summary (dataset=test_set0226)

- Backend: `anthropic`
- Model: `claude-sonnet-4-6`
- Scan scope: `LAJ_score_outputs/dataset=test_set0226/**/backend=anthropic/**`
- Date: `2026-03-18`

## Coverage

- Required files present under project rules: `56/56`
- Missing required configs: `0`
- Duplicate run-stamps for same config+criterion: `0`

## Big Error Totals

- Files with big errors: `31`
- `status=error` rows: `32` (across `30` files)
- `parse_error` rows: `1` (in `1` file)
- `ai_score=-1` rows: `33` (across `31` files)
- `rows < 800`: `0` files

## Recoverability Check

- Bad rows scanned for raw extraction: `33`
- Recoverable valid scores from raw output (`c1-c3: {0,1}`, `c4-c7: {1..5}`): `0`
- Interpretation:
  - `32/33` bad rows have empty `ai_reasoning` and empty `judge_output_raw`
  - `1/33` row has non-empty raw output but `final_score=-1` (invalid)

## Main Failure Pattern

Most errors are empty judge outputs on otherwise 800-row files, resulting in `status=error` and `ai_score=-1` for a small number of rows per file.

## Single Parse Error File

- `safety/method=dspy_opt_prompt/.../c2_individualized_medical_advice_v1.jsonl`
