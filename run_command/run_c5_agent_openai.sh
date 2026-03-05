#!/bin/bash
# run_command/run_c5_factual_acc_openai.sh
# C5 factual accuracy agent runner (OpenAI full-text oracle)
# Supports smoke test and full modes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$WORKSPACE_ROOT"

export PYTHONPATH="$WORKSPACE_ROOT:$PYTHONPATH"

# ============================================================
# CONFIGURATION
# ============================================================
INPUT_JSONL="${INPUT_JSONL:-data_eval/test_set0226.jsonl}"
DATASET_ID="${DATASET_ID:-test_set0226}"
SKILLS_DIR="${SKILLS_DIR:-skills/reliability/c5_factual_accuracy}"
TEXT_FOLDER="${TEXT_FOLDER:-ICF_text_cleaned}"
OUTPUT_ROOT="${OUTPUT_ROOT:-LAJ_score_outputs}"
KEY_FILE="${KEY_FILE:-prompt_comparison_script/gpt_key.txt}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.2}"

# Token budgets
MAX_TOKENS="${MAX_TOKENS:-768}"

# Flags
FORCE="${FORCE:-false}"
INCLUDE_RAW_OUTPUT="${INCLUDE_RAW_OUTPUT:-true}"

# Method: agent | dspy_opt_prompt
METHOD="${METHOD:-agent}"

# DSPy options (only used when METHOD=dspy_opt_prompt)
COMPILED_ARTIFACTS_ROOT="${COMPILED_ARTIFACTS_ROOT:-dspy_engine/compiled_artifacts}"
COPRO_DEPTH="${COPRO_DEPTH:-1}"
COPRO_CANDIDATES="${COPRO_CANDIDATES:-3}"

# MODE: test | full
MODE="${MODE:-test}"
TEST_ROWS="${TEST_ROWS:-5}"

# ============================================================
# Preflight checks
# ============================================================
echo "=========================================="
echo "Preflight Checks"
echo "=========================================="

if [[ ! -f "$INPUT_JSONL" ]]; then
  echo "❌ ERROR: Missing input file: $INPUT_JSONL"
  exit 1
fi

if [[ ! -f "$KEY_FILE" ]]; then
  echo "❌ ERROR: Missing API key file: $KEY_FILE"
  exit 1
fi

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "❌ ERROR: Missing skills directory: $SKILLS_DIR"
  exit 1
fi

if [[ ! -d "$TEXT_FOLDER" ]]; then
  echo "⚠️  WARNING: Text folder not found: $TEXT_FOLDER"
  echo "   Script will continue but may not find ICF full text files"
fi

# Validate MODE
case "$MODE" in
  test|full) ;;
  *)
    echo "❌ ERROR: MODE must be 'test' or 'full'. Got: $MODE"
    exit 1
    ;;
esac

# Validate METHOD
case "$METHOD" in
  agent|dspy_opt_prompt) ;;
  *)
    echo "❌ ERROR: METHOD must be 'agent' or 'dspy_opt_prompt'. Got: $METHOD"
    exit 1
    ;;
esac

# Check compiled artifacts if using dspy_opt_prompt
if [ "$METHOD" = "dspy_opt_prompt" ]; then
  if [[ ! -d "$COMPILED_ARTIFACTS_ROOT" ]]; then
    echo "⚠️  WARNING: Compiled artifacts root not found: $COMPILED_ARTIFACTS_ROOT"
    echo "   Script will continue but may fail if artifacts are missing"
  fi
fi

echo "✅ All checks passed"
echo ""

# Handle MODE
if [ "$MODE" = "test" ]; then
  USE_TEST_ROWS=true
  echo "=========================================="
  echo "C5 Factual Accuracy - SMOKE TEST MODE"
  echo "=========================================="
elif [ "$MODE" = "full" ]; then
  USE_TEST_ROWS=false
  echo "=========================================="
  echo "C5 Factual Accuracy - FULL MODE"
  echo "=========================================="
fi

echo "Input JSONL:              $INPUT_JSONL"
echo "Dataset ID:               $DATASET_ID"
echo "Method:                   $METHOD"
echo "Skills directory:         $SKILLS_DIR"
echo "Text folder (ICF):        $TEXT_FOLDER"
echo "Judge model:              $JUDGE_MODEL"
echo "Max tokens:               $MAX_TOKENS"
if [ "$METHOD" = "dspy_opt_prompt" ]; then
  echo "Compiled artifacts:       $COMPILED_ARTIFACTS_ROOT"
  echo "COPRO depth:             $COPRO_DEPTH"
  echo "COPRO candidates:        $COPRO_CANDIDATES"
fi
if [ "$USE_TEST_ROWS" = true ]; then
  echo "Test rows:                $TEST_ROWS"
fi
echo "Force (disable resume):   $FORCE"
echo "Include raw output:       $INCLUDE_RAW_OUTPUT"
echo "=========================================="
echo ""

# Build command
CMD=(python agent/run_skill_scoring_acc_openai.py
  --input_jsonl "$INPUT_JSONL"
  --dataset_id "$DATASET_ID"
  --skills_dir "$SKILLS_DIR"
  --text_folder "$TEXT_FOLDER"
  --judge_model "$JUDGE_MODEL"
  --key_file "$KEY_FILE"
  --output_root "$OUTPUT_ROOT"
  --max_tokens "$MAX_TOKENS"
  --method "$METHOD"
)

# Add DSPy-specific arguments if using dspy_opt_prompt
if [ "$METHOD" = "dspy_opt_prompt" ]; then
  CMD+=(--compiled_artifacts_root "$COMPILED_ARTIFACTS_ROOT")
  CMD+=(--copro_depth "$COPRO_DEPTH")
  CMD+=(--copro_candidates "$COPRO_CANDIDATES")
fi

if [ "$USE_TEST_ROWS" = true ] && [ "$TEST_ROWS" -gt 0 ]; then
  CMD+=(--test_rows "$TEST_ROWS")
fi

if [ "$FORCE" = "true" ]; then
  CMD+=(--force)
fi

if [ "$INCLUDE_RAW_OUTPUT" = "true" ]; then
  CMD+=(--include_raw_output)
fi

# Execute
"${CMD[@]}"

echo ""
echo "=========================================="
echo "✅ Done. Outputs under: $OUTPUT_ROOT/dataset=$DATASET_ID/"
echo "=========================================="