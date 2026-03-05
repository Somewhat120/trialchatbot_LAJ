#!/bin/bash
# bash_noRAG_agent/claude_run_agent.sh
# Agent method scoring runner for Anthropic Claude (Messages API backend)
# No GPU / vLLM needed; runs from login node or CPU job.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$WORKSPACE_ROOT"

# Add workspace root to PYTHONPATH so dspy_engine can be imported
export PYTHONPATH="$WORKSPACE_ROOT:$PYTHONPATH"

# ============================================================
# CONFIGURATION (override via env vars if desired)
# ============================================================
INPUT_JSONL="${INPUT_JSONL:-data_eval/round1.jsonl}"
DATASET_ID="${DATASET_ID:-round1_claude_test}"
SKILLS_ROOT="${SKILLS_ROOT:-skills}"
OUTPUT_ROOT="${OUTPUT_ROOT:-LAJ_score_outputs}"
KEY_FILE="${KEY_FILE:-prompt_comparison_script/anthropic_key.txt}"
JUDGE_MODEL="${JUDGE_MODEL:-claude-sonnet-4-20250514}"

# METHODS: agent | dspy_noopt | dspy_opt_prompt
METHOD="${METHOD:-agent}"

# MODE: test | full
MODE="${MODE:-test}"
TEST_ROWS="${TEST_ROWS:-5}"

# Token budgets
MAX_TOKENS_TEST="${MAX_TOKENS_TEST:-2048}"
MAX_TOKENS_FULL="${MAX_TOKENS_FULL:-2048}"

# Generation parameters
TEMPERATURE="${TEMPERATURE:-0.0}"
TOP_P="${TOP_P:-1.0}"

# Flags
FORCE="${FORCE:-false}"
INCLUDE_RAW_OUTPUT="${INCLUDE_RAW_OUTPUT:-true}"

# ============================================================
# Validate METHOD
# ============================================================
case "$METHOD" in
  agent|dspy_noopt|dspy_opt_prompt) ;;
  *)
    echo "ERROR: METHOD must be one of: agent | dspy_noopt | dspy_opt_prompt"
    echo "   Got: $METHOD"
    exit 1
    ;;
esac

# ============================================================
# Select settings based on MODE
# ============================================================
if [ "$MODE" = "test" ]; then
  MAX_TOKENS="$MAX_TOKENS_TEST"
  USE_TEST_ROWS=true
  echo "=========================================="
  echo "Claude Runner - TEST MODE ($TEST_ROWS rows)"
  echo "=========================================="
elif [ "$MODE" = "full" ]; then
  MAX_TOKENS="$MAX_TOKENS_FULL"
  USE_TEST_ROWS=false
  echo "=========================================="
  echo "Claude Runner - FULL MODE (all rows)"
  echo "=========================================="
else
  echo "ERROR: MODE must be 'test' or 'full'. Got: $MODE"
  exit 1
fi

# ============================================================
# Preflight checks
# ============================================================
if [[ ! -f "$INPUT_JSONL" ]]; then
  echo "ERROR: Missing input file: $INPUT_JSONL"
  exit 1
fi

if [[ ! -f "$KEY_FILE" ]]; then
  echo "ERROR: Missing API key file: $KEY_FILE"
  echo "Create it with:  echo 'sk-ant-...' > $KEY_FILE"
  exit 1
fi

if [[ ! -d "$SKILLS_ROOT" ]]; then
  echo "ERROR: Missing skills root: $SKILLS_ROOT"
  exit 1
fi

# ============================================================
# Build command
# ============================================================
CMD=(python3 agent/run_skill_scoring_loop_claude.py
  --input_jsonl "$INPUT_JSONL"
  --dataset_id "$DATASET_ID"
  --skills_root "$SKILLS_ROOT"
  --judge_model "$JUDGE_MODEL"
  --key_file "$KEY_FILE"
  --method "$METHOD"
  --output_root "$OUTPUT_ROOT"
  --max_tokens "$MAX_TOKENS"
  --temperature "$TEMPERATURE"
  --top_p "$TOP_P"
)

# Add --test_rows only in test mode
if [ "$USE_TEST_ROWS" = true ]; then
  CMD+=(--test_rows "$TEST_ROWS")
fi

# Include raw output (recommended for debugging / audit)
if [ "$INCLUDE_RAW_OUTPUT" = "true" ]; then
  CMD+=(--include_raw_output)
fi

if [ "$FORCE" = "true" ]; then
  CMD+=(--force)
fi

# ============================================================
# Print run config
# ============================================================
echo "Input:              $INPUT_JSONL"
echo "Dataset ID:         $DATASET_ID"
echo "Skills root:        $SKILLS_ROOT"
echo "Judge model:        $JUDGE_MODEL"
echo "Method:             $METHOD"
echo "Mode:               $MODE"
echo "Max tokens:         $MAX_TOKENS"
echo "Temperature:        $TEMPERATURE"
echo "Top-p:              $TOP_P"
echo "Force:              $FORCE"
echo "Include raw output: $INCLUDE_RAW_OUTPUT"
if [ "$USE_TEST_ROWS" = true ]; then
  echo "Test rows:          $TEST_ROWS"
fi
echo "=========================================="
echo

# ============================================================
# Run
# ============================================================
"${CMD[@]}"

echo
echo "=========================================="
echo "Complete!"
echo "Output: $OUTPUT_ROOT/dataset=$DATASET_ID/"
echo "=========================================="
