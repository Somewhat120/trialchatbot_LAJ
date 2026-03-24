#!/bin/bash
# repair_gemini_test_set0226.sh
# Repair failed rows in Gemini gemini-3-pro-preview outputs for test_set0226.
# Strategy: strip bad rows from JSONL files, then re-run the pipeline scripts
# so their resume logic fills in only the missing qa_ids.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$WORKSPACE_ROOT"

export PYTHONPATH="$WORKSPACE_ROOT:${PYTHONPATH:-}"

# ============================================================
# CONFIGURATION
# ============================================================
INPUT_JSONL="data_eval/test_set0226.jsonl"
DATASET_ID="test_set0226"
SKILLS_ROOT="skills"
OUTPUT_ROOT="LAJ_score_outputs"
KEY_FILE="prompt_comparison_script/gemini_key.txt"
JUDGE_MODEL="gemini-3-pro-preview"
TEXT_FOLDER="ICF_text_cleaned"
RUN_STAMP="${RUN_STAMP:-20260319_172018}"
MAX_TOKENS="${MAX_TOKENS:-2048}"
MAX_INPUT_CHARS="${MAX_INPUT_CHARS:-12000}"
WORKERS="${WORKERS:-6}"

OUTPUT_BASE="$OUTPUT_ROOT/dataset=$DATASET_ID"

# ============================================================
# Preflight checks
# ============================================================
echo "=========================================="
echo "Preflight Checks"
echo "=========================================="
[[ ! -f "$INPUT_JSONL" ]] && { echo "ERROR: Missing input file: $INPUT_JSONL"; exit 1; }
[[ ! -f "$KEY_FILE" ]] && { echo "ERROR: Missing API key file: $KEY_FILE"; exit 1; }
[[ ! -d "$SKILLS_ROOT" ]] && { echo "ERROR: Missing skills root: $SKILLS_ROOT"; exit 1; }
echo "All checks passed"
echo "=========================================="
echo ""

# ============================================================
# PHASE A: Scan for bad rows
# ============================================================
echo "=========================================="
echo "PHASE A: Scanning for bad rows"
echo "=========================================="

TOTAL_BAD=0
FILES_WITH_BAD=0

while IFS= read -r f; do
    bad=$(python3 -c "
import json, sys
count = 0
with open('$f') as fh:
    for line in fh:
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        if r.get('status') == 'error' or r.get('parse_error') not in (None, '', False) or r.get('ai_score') == -1:
            count += 1
print(count)
")
    if [[ "$bad" -gt 0 ]]; then
        short="${f#$WORKSPACE_ROOT/}"
        echo "  BAD=$bad  $short"
        TOTAL_BAD=$((TOTAL_BAD + bad))
        FILES_WITH_BAD=$((FILES_WITH_BAD + 1))
    fi
done < <(find "$OUTPUT_BASE" -path '*backend=gemini*' -path "*model=$JUDGE_MODEL*" -name '*.jsonl' ! -name '*.bak' | sort)

echo ""
echo "Total bad rows: $TOTAL_BAD across $FILES_WITH_BAD files"

if [[ "$TOTAL_BAD" -eq 0 ]]; then
    echo "No bad rows found. Nothing to repair."
    exit 0
fi
echo ""

# ============================================================
# PHASE B: Deduplicate + Backup + Strip bad rows
# ============================================================
echo "=========================================="
echo "PHASE B: Deduplicate, backup, and strip bad rows"
echo "=========================================="

while IFS= read -r f; do
    python3 -c "
import json, sys, shutil

path = '$f'
rows = []
with open(path) as fh:
    for line in fh:
        line = line.strip()
        if not line: continue
        rows.append(json.loads(line))

# Deduplicate: keep last occurrence per qa_id
seen = {}
for r in rows:
    qid = r.get('qa_id')
    if qid is not None:
        seen[qid] = r
    else:
        # fallback: use record_id + run_key
        key = r.get('run_key') or r.get('record_id') or id(r)
        seen[key] = r

deduped = sorted(seen.values(), key=lambda x: x.get('qa_id', 0) if x.get('qa_id') is not None else 0)

# Strip bad rows
good = []
bad = 0
for r in deduped:
    if r.get('status') == 'error' or r.get('parse_error') not in (None, '', False) or r.get('ai_score') == -1:
        bad += 1
    else:
        good.append(r)

orig_count = len(rows)
dedup_count = len(deduped)
if bad > 0 or orig_count != dedup_count:
    shutil.copy2(path, path + '.bak')
    with open(path, 'w') as fh:
        for r in good:
            fh.write(json.dumps(r) + '\n')
    parts = []
    if orig_count != dedup_count:
        parts.append(f'deduped {orig_count}->{dedup_count}')
    if bad > 0:
        parts.append(f'stripped {bad} bad')
    msg = ', '.join(parts)
    print(f'  {msg}, kept {len(good)}: {path}')
"
done < <(find "$OUTPUT_BASE" -path '*backend=gemini*' -path "*model=$JUDGE_MODEL*" -name '*.jsonl' ! -name '*.bak' | sort)

echo ""
echo "Strip complete."
echo ""

# ============================================================
# PHASE C: Re-run pipeline (resume fills missing qa_ids only)
# ============================================================
echo "=========================================="
echo "PHASE C: Re-running pipeline to fill gaps"
echo "=========================================="

# --- Step 1: Prompt noRAG zeroshot ---
echo "--- Step 1/8: Prompt noRAG zeroshot ---"
python prompt_comparison_script/02_LAJ_noRAG_loop_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --run_stamp "$RUN_STAMP" \
    --model "$JUDGE_MODEL" \
    --max_tokens "$MAX_TOKENS" \
    --workers "$WORKERS" \
    --zeroshot_only

# --- Step 2: Prompt noRAG fewshot ---
echo "--- Step 2/8: Prompt noRAG fewshot ---"
python prompt_comparison_script/02_LAJ_noRAG_loop_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --run_stamp "$RUN_STAMP" \
    --model "$JUDGE_MODEL" \
    --max_tokens "$MAX_TOKENS" \
    --workers "$WORKERS" \
    --fewshot_only

# --- Step 3: Prompt Oracle zeroshot ---
echo "--- Step 3/8: Prompt Oracle zeroshot ---"
python prompt_comparison_script/02_LAJ_oracle_loop_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --run_stamp "$RUN_STAMP" \
    --model "$JUDGE_MODEL" \
    --max_tokens "$MAX_TOKENS" \
    --workers "$WORKERS" \
    --text_folder "$TEXT_FOLDER" \
    --zeroshot_only \
    --include_raw_output

# --- Step 4: Prompt Oracle fewshot ---
echo "--- Step 4/8: Prompt Oracle fewshot ---"
python prompt_comparison_script/02_LAJ_oracle_loop_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --run_stamp "$RUN_STAMP" \
    --model "$JUDGE_MODEL" \
    --max_tokens "$MAX_TOKENS" \
    --workers "$WORKERS" \
    --text_folder "$TEXT_FOLDER" \
    --fewshot_only \
    --include_raw_output

# --- Step 5: Agent C1-C4,C6-C7 (agent method) ---
echo "--- Step 5/8: Agent scoring (agent method) ---"
python agent/run_skill_scoring_loop_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --skills_root "$SKILLS_ROOT" \
    --judge_model "$JUDGE_MODEL" \
    --key_file "$KEY_FILE" \
    --method agent \
    --output_root "$OUTPUT_ROOT" \
    --max_tokens "$MAX_TOKENS" \
    --run_stamp "$RUN_STAMP" \
    --include_raw_output

# --- Step 6: Agent C1-C4,C6-C7 (dspy_opt_prompt method) ---
echo "--- Step 6/8: Agent scoring (dspy_opt_prompt method) ---"
python agent/run_skill_scoring_loop_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --skills_root "$SKILLS_ROOT" \
    --judge_model "$JUDGE_MODEL" \
    --key_file "$KEY_FILE" \
    --method dspy_opt_prompt \
    --output_root "$OUTPUT_ROOT" \
    --max_tokens "$MAX_TOKENS" \
    --compiled_artifacts_root "dspy_engine/compiled_artifacts" \
    --copro_depth 1 \
    --copro_candidates 3 \
    --run_stamp "$RUN_STAMP" \
    --include_raw_output

# --- Step 7: C5 Agent (agent method) ---
echo "--- Step 7/8: C5 Agent scoring (agent method) ---"
python agent/run_skill_scoring_acc_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --skills_dir "skills/reliability/c5_factual_accuracy" \
    --text_folder "$TEXT_FOLDER" \
    --judge_model "$JUDGE_MODEL" \
    --key_file "$KEY_FILE" \
    --output_root "$OUTPUT_ROOT" \
    --max_tokens "$MAX_TOKENS" \
    --max_input_chars "$MAX_INPUT_CHARS" \
    --run_stamp "$RUN_STAMP" \
    --include_raw_output

# --- Step 8: C5 Agent (dspy_opt_prompt method) ---
echo "--- Step 8/8: C5 Agent scoring (dspy_opt_prompt method) ---"
python agent/run_skill_scoring_acc_gemini.py \
    --input_jsonl "$INPUT_JSONL" \
    --dataset_id "$DATASET_ID" \
    --skills_dir "skills/reliability/c5_factual_accuracy" \
    --text_folder "$TEXT_FOLDER" \
    --judge_model "$JUDGE_MODEL" \
    --key_file "$KEY_FILE" \
    --output_root "$OUTPUT_ROOT" \
    --max_tokens "$MAX_TOKENS" \
    --max_input_chars "$MAX_INPUT_CHARS" \
    --method dspy_opt_prompt \
    --compiled_artifacts_root "dspy_engine/compiled_artifacts" \
    --copro_depth 1 \
    --copro_candidates 3 \
    --run_stamp "$RUN_STAMP" \
    --include_raw_output

echo ""
echo "Re-run complete."
echo ""

# ============================================================
# PHASE D: Validate
# ============================================================
echo "=========================================="
echo "PHASE D: Validation"
echo "=========================================="

PASS_COUNT=0
FAIL_COUNT=0

while IFS= read -r f; do
    short="${f#$WORKSPACE_ROOT/}"
    result=$(python3 -c "
import json, sys

path = '$f'
rows = []
with open(path) as fh:
    for line in fh:
        line = line.strip()
        if not line: continue
        rows.append(json.loads(line))

total = len(rows)
errors = sum(1 for r in rows if r.get('status') == 'error')
parse_errs = sum(1 for r in rows if r.get('parse_error') not in (None, '', False))
neg1 = sum(1 for r in rows if r.get('ai_score') == -1)

# Determine criterion for score range check
fname = path.lower()
is_binary = any(c in fname for c in ['c1_', 'c2_', 'c3_'])
range_bad = 0
for r in rows:
    s = r.get('ai_score')
    if s is None: continue
    if is_binary:
        if s not in (0, 1): range_bad += 1
    else:
        if s not in (1, 2, 3, 4, 5): range_bad += 1

# Check unique qa_ids
qa_ids = [r.get('qa_id') or r.get('record_id') for r in rows]
dupes = len(qa_ids) - len(set(qa_ids))

issues = []
if total != 800: issues.append(f'rows={total}')
if errors > 0: issues.append(f'status_error={errors}')
if parse_errs > 0: issues.append(f'parse_error={parse_errs}')
if neg1 > 0: issues.append(f'ai_score_neg1={neg1}')
if range_bad > 0: issues.append(f'score_range_bad={range_bad}')
if dupes > 0: issues.append(f'duplicate_qa_ids={dupes}')

if issues:
    print('FAIL ' + ', '.join(issues))
else:
    print('PASS')
")
    if [[ "$result" == "PASS" ]]; then
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo "  FAIL: $short  ($result)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done < <(find "$OUTPUT_BASE" -path '*backend=gemini*' -path "*model=$JUDGE_MODEL*" -name '*.jsonl' ! -name '*.bak' | sort)

echo ""
echo "=========================================="
echo "Validation Summary: $PASS_COUNT PASS, $FAIL_COUNT FAIL"
echo "=========================================="

if [[ "$FAIL_COUNT" -eq 0 ]]; then
    echo "All files passed. Cleaning up .bak files..."
    find "$OUTPUT_BASE" -path '*backend=gemini*' -path "*model=$JUDGE_MODEL*" -name '*.bak' -delete
    echo "Done. Ready to push."
else
    echo "Some files still have issues. Backup .bak files preserved."
    echo "Re-run this script to attempt another repair pass."
fi
