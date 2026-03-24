#!/bin/bash
# dspy_engine/compile/run_compile_c5_factual_acc_claude.sh
# C5 Factual Accuracy - COPRO skill optimization compile script (Anthropic Claude)
# Supports smoke test and full modes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$WORKSPACE_ROOT"

export PYTHONPATH="$WORKSPACE_ROOT:${PYTHONPATH:-}"

# ============================================================
# CONFIGURATION
# ============================================================
CRITERION_ID="${CRITERION_ID:-reliability_c5_factual_accuracy}"
SKILL_MD="${SKILL_MD:-skills/reliability/c5_factual_accuracy/SKILL_v1.md}"
DEV_JSONL="${DEV_JSONL:-data_preparation/development/5_factual_acc_new.jsonl}"
LABEL_KEY="${LABEL_KEY:-Level of factual accuracy}"
TEXT_FOLDER="${TEXT_FOLDER:-dev_icf_vanilla}"

JUDGE_MODEL="${JUDGE_MODEL:-claude-opus-4-6}"
REWRITER_MODEL="${REWRITER_MODEL:-}"
KEY_FILE="${KEY_FILE:-prompt_comparison_script/anthropic_key.txt}"
COMPILED_ROOT="${COMPILED_ROOT:-dspy_engine/compiled_artifacts}"

# Optimization parameters
DEPTH="${DEPTH:-1}"
NUM_CANDIDATES="${NUM_CANDIDATES:-3}"
MAX_SKILL_TOKENS="${MAX_SKILL_TOKENS:-2048}"
MAX_JUDGE_OUTPUT_TOKENS="${MAX_JUDGE_OUTPUT_TOKENS:-2048}"

# Rewrite parameters
REWRITE_TEMPERATURE="${REWRITE_TEMPERATURE:-0.8}"
REWRITE_TOP_P="${REWRITE_TOP_P:-1.0}"
JUDGE_TEMPERATURE="${JUDGE_TEMPERATURE:-0.0}"
JUDGE_TOP_P="${JUDGE_TOP_P:-1.0}"

# MODE: test | full
MODE="${MODE:-test}"
LIMIT="${LIMIT:-5}"

# ============================================================
# Preflight checks
# ============================================================
echo "=========================================="
echo "Preflight Checks"
echo "=========================================="

if [[ ! -f "$SKILL_MD" ]]; then
  echo "ERROR: Missing skill file: $SKILL_MD"
  exit 1
fi

if [[ ! -f "$DEV_JSONL" ]]; then
  echo "ERROR: Missing dev JSONL: $DEV_JSONL"
  exit 1
fi

if [[ ! -f "$KEY_FILE" ]]; then
  echo "ERROR: Missing API key file: $KEY_FILE"
  exit 1
fi

if [[ -n "$TEXT_FOLDER" && ! -d "$TEXT_FOLDER" ]]; then
  echo "WARNING: Text folder not found: $TEXT_FOLDER"
  echo "   Will use evidence_snippet from JSONL only"
fi

# Validate MODE
case "$MODE" in
  test|full) ;;
  *)
    echo "ERROR: MODE must be 'test' or 'full'. Got: $MODE"
    exit 1
    ;;
esac

echo "All checks passed"
echo ""

# Handle MODE
if [ "$MODE" = "test" ]; then
  USE_LIMIT=true
  echo "=========================================="
  echo "C5 Factual Accuracy (Claude) - SMOKE TEST MODE"
  echo "=========================================="
elif [ "$MODE" = "full" ]; then
  USE_LIMIT=false
  echo "=========================================="
  echo "C5 Factual Accuracy (Claude) - FULL MODE"
  echo "=========================================="
fi

echo "Criterion ID:            $CRITERION_ID"
echo "Skill file:              $SKILL_MD"
echo "Dev JSONL:               $DEV_JSONL"
echo "Label key:               $LABEL_KEY"
echo "Text folder (ICF):        $TEXT_FOLDER"
echo "Judge model:             $JUDGE_MODEL"
if [[ -n "$REWRITER_MODEL" ]]; then
  echo "Rewriter model:          $REWRITER_MODEL"
else
  echo "Rewriter model:          $JUDGE_MODEL (default)"
fi
echo "Compiled root:           $COMPILED_ROOT"
echo "Depth:                   $DEPTH"
echo "Num candidates:          $NUM_CANDIDATES"
echo "Max skill tokens:        $MAX_SKILL_TOKENS"
echo "Max judge output tokens: $MAX_JUDGE_OUTPUT_TOKENS"
if [ "$USE_LIMIT" = true ]; then
  echo "Limit (test mode):       $LIMIT rows"
fi
echo "=========================================="
echo ""

# Build command
CMD=(uv run python dspy_engine/compile/compile_dspy_copro_skill_c5_claude.py
  --criterion_id "$CRITERION_ID"
  --skill_md "$SKILL_MD"
  --dev_jsonl "$DEV_JSONL"
  --label_key "$LABEL_KEY"
  --judge_model "$JUDGE_MODEL"
  --key_file "$KEY_FILE"
  --compiled_root "$COMPILED_ROOT"
  --depth "$DEPTH"
  --num_candidates "$NUM_CANDIDATES"
  --max_skill_tokens "$MAX_SKILL_TOKENS"
  --max_judge_output_tokens "$MAX_JUDGE_OUTPUT_TOKENS"
  --rewrite_temperature "$REWRITE_TEMPERATURE"
  --rewrite_top_p "$REWRITE_TOP_P"
  --judge_temperature "$JUDGE_TEMPERATURE"
  --judge_top_p "$JUDGE_TOP_P"
)

# Add optional parameters
if [[ -n "$REWRITER_MODEL" ]]; then
  CMD+=(--rewriter_model "$REWRITER_MODEL")
fi

if [[ -n "$TEXT_FOLDER" ]]; then
  CMD+=(--text_folder "$TEXT_FOLDER")
fi

if [ "$USE_LIMIT" = true ] && [ "$LIMIT" -gt 0 ]; then
  CMD+=(--limit "$LIMIT")
fi

# Execute
"${CMD[@]}"

echo ""
echo "=========================================="
echo "Compilation complete!"
echo "=========================================="
echo "Artifacts saved to: $COMPILED_ROOT/criterion=$CRITERION_ID/"
echo ""
echo "To use the compiled skill at runtime:"
echo "  Use method=dspy_opt_prompt in your scoring runner"
echo "  The runner will automatically load best_skill.md"
echo "=========================================="
