#!/bin/bash
# run_all_claude.sh
# Complete scoring pipeline for test_set0226.jsonl using Anthropic Claude

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
KEY_FILE="prompt_comparison_script/anthropic_key.txt"
# Use env JUDGE_MODEL if set, else default
JUDGE_MODEL="${JUDGE_MODEL:-claude-opus-4-6}"
TEXT_FOLDER="ICF_text_cleaned"

# Token budgets
MAX_TOKENS="${MAX_TOKENS:-1024}"

# Flags
FORCE="${FORCE:-false}"
INCLUDE_RAW_OUTPUT="${INCLUDE_RAW_OUTPUT:-true}"
WORKERS="${WORKERS:-8}"

# Which steps to run: "prompt_only" | "agent_only" | "all"
RUN_MODE="${RUN_MODE:-all}"

# Test mode for agent steps (0 = all rows)
TEST_ROWS="${TEST_ROWS:-0}"

# Optional filters
PROMPT_VARIATION="${PROMPT_VARIATION:-}"
PROMPT_BASE="${PROMPT_BASE:-}"
PROMPT_METHOD="${PROMPT_METHOD:-all}" # zeroshot | fewshot | all

# ============================================================
# Preflight checks
# ============================================================
echo "=========================================="
echo "Preflight Checks"
echo "=========================================="

[[ ! -f "$INPUT_JSONL" ]] && { echo "ERROR: Missing input file: $INPUT_JSONL"; exit 1; }
[[ ! -f "$KEY_FILE" ]] && { echo "ERROR: Missing API key file: $KEY_FILE"; exit 1; }
[[ ! -d "$SKILLS_ROOT" ]] && { echo "ERROR: Missing skills root: $SKILLS_ROOT"; exit 1; }
[[ ! -d "$TEXT_FOLDER" ]] && { echo "ERROR: Missing text folder: $TEXT_FOLDER"; exit 1; }

RUN_STAMP="${RUN_STAMP:-$(date +"%Y%m%d_%H%M%S")}"

echo "All checks passed"
echo "Run mode:            $RUN_MODE"
echo "Run stamp:           $RUN_STAMP"
echo "=========================================="
echo ""

# Internal function to handle the binary split logic for prompts
run_prompt_step() {
    local script_path=$1
    local extra_args=${2:-""}

    local methods_to_run=()
    if [[ "$PROMPT_METHOD" == "all" ]]; then
        methods_to_run=("zeroshot" "fewshot")
    else
        methods_to_run=("$PROMPT_METHOD")
    fi

    for m in "${methods_to_run[@]}"; do
        echo "--- Running Method: $m ---"
        uv run python "$script_path" \
            --input_jsonl "$INPUT_JSONL" \
            --dataset_id "$DATASET_ID" \
            --run_stamp "$RUN_STAMP" \
            --model "$JUDGE_MODEL" \
            --max_tokens "$MAX_TOKENS" \
            --workers "$WORKERS" \
            $extra_args \
            ${PROMPT_VARIATION:+--prompt_variation} ${PROMPT_VARIATION:+"$PROMPT_VARIATION"} \
            ${PROMPT_BASE:+--prompt_base} ${PROMPT_BASE:+"$PROMPT_BASE"} \
            $([ "$m" = "zeroshot" ] && echo "--zeroshot_only") \
            $([ "$m" = "fewshot" ] && echo "--fewshot_only") \
            $([ "$INCLUDE_RAW_OUTPUT" = "true" ] && echo "--include_raw_output") \
            $([ "$FORCE" = "true" ] && echo "--force")
    done
}

if [[ "$RUN_MODE" != "agent_only" ]]; then

# ============================================================
# STEP 1: Prompt-based Scoring (noRAG)
# ============================================================
echo "=========================================="
echo "STEP 1: Prompt Scoring (noRAG)"
echo "=========================================="
run_prompt_step "prompt_comparison_script/02_LAJ_noRAG_loop_claude.py" ""
echo "Prompt scoring (noRAG) complete!"
echo ""

# ============================================================
# STEP 2: Prompt-based Scoring (Oracle - with ICF text)
# ============================================================
echo "=========================================="
echo "STEP 2: Prompt Scoring (Oracle)"
echo "=========================================="
run_prompt_step "prompt_comparison_script/02_LAJ_oracle_loop_claude.py" "--text_folder $TEXT_FOLDER"
echo "Prompt scoring (Oracle) complete!"
echo ""

fi  # End of prompt steps conditional

# Skip agent steps if only running prompts
if [[ "$RUN_MODE" != "prompt_only" ]]; then

# ============================================================
# STEP 3: Agent-based Scoring (agent method) - C1-C4, C6-C7
# ============================================================
echo "=========================================="
echo "STEP 3: Agent Scoring (agent method) - C1-C4, C6-C7"
echo "=========================================="
uv run python agent/run_skill_scoring_loop_claude.py \
  --input_jsonl "$INPUT_JSONL" \
  --dataset_id "$DATASET_ID" \
  --skills_root "$SKILLS_ROOT" \
  --judge_model "$JUDGE_MODEL" \
  --key_file "$KEY_FILE" \
  --method agent \
  --output_root "$OUTPUT_ROOT" \
  --max_tokens "$MAX_TOKENS" \
  --run_stamp "$RUN_STAMP" \
  $([ "$TEST_ROWS" -gt 0 ] && echo "--test_rows $TEST_ROWS") \
  $([ "$INCLUDE_RAW_OUTPUT" = "true" ] && echo "--include_raw_output") \
  $([ "$FORCE" = "true" ] && echo "--force")

echo "Agent scoring (agent method) complete!"
echo ""

# ============================================================
# STEP 4: Agent-based Scoring (dspy_noopt method) - SKIPPED
# ============================================================
# echo "=========================================="
# echo "STEP 4: Agent Scoring (dspy_noopt method) - SKIPPED"
# echo "=========================================="
# uv run python agent/run_skill_scoring_loop_claude.py ...

# ============================================================
# STEP 5: Agent-based Scoring (dspy_opt_prompt method) - C1-C4, C6-C7
# ============================================================
echo "=========================================="
echo "STEP 5: Agent Scoring (dspy_opt_prompt method) - C1-C4, C6-C7"
echo "=========================================="
uv run python agent/run_skill_scoring_loop_claude.py \
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
  $([ "$TEST_ROWS" -gt 0 ] && echo "--test_rows $TEST_ROWS") \
  $([ "$INCLUDE_RAW_OUTPUT" = "true" ] && echo "--include_raw_output") \
  $([ "$FORCE" = "true" ] && echo "--force")

echo "Agent scoring (dspy_opt_prompt method) complete!"
echo ""

# ============================================================
# STEP 6: C5 Agent Scoring (agent method) - Full-text Oracle
# ============================================================
echo "=========================================="
echo "STEP 6: C5 Agent Scoring (agent method)"
echo "=========================================="
uv run python agent/run_skill_scoring_acc_claude.py \
  --input_jsonl "$INPUT_JSONL" \
  --dataset_id "$DATASET_ID" \
  --skills_dir "skills/reliability/c5_factual_accuracy" \
  --text_folder "$TEXT_FOLDER" \
  --judge_model "$JUDGE_MODEL" \
  --key_file "$KEY_FILE" \
  --output_root "$OUTPUT_ROOT" \
  --max_tokens "$MAX_TOKENS" \
  --run_stamp "$RUN_STAMP" \
  $([ "$TEST_ROWS" -gt 0 ] && echo "--test_rows $TEST_ROWS") \
  $([ "$INCLUDE_RAW_OUTPUT" = "true" ] && echo "--include_raw_output") \
  $([ "$FORCE" = "true" ] && echo "--force")

echo "C5 Agent scoring (agent method) complete!"
echo ""

# ============================================================
# STEP 7: C5 Agent Scoring (dspy_opt_prompt method) - Full-text Oracle
# ============================================================
echo "=========================================="
echo "STEP 7: C5 Agent Scoring (dspy_opt_prompt method)"
echo "=========================================="
uv run python agent/run_skill_scoring_acc_claude.py \
  --input_jsonl "$INPUT_JSONL" \
  --dataset_id "$DATASET_ID" \
  --skills_dir "skills/reliability/c5_factual_accuracy" \
  --text_folder "$TEXT_FOLDER" \
  --judge_model "$JUDGE_MODEL" \
  --key_file "$KEY_FILE" \
  --output_root "$OUTPUT_ROOT" \
  --max_tokens "$MAX_TOKENS" \
  --method dspy_opt_prompt \
  --compiled_artifacts_root "dspy_engine/compiled_artifacts" \
  --copro_depth 1 \
  --copro_candidates 3 \
  --run_stamp "$RUN_STAMP" \
  $([ "$TEST_ROWS" -gt 0 ] && echo "--test_rows $TEST_ROWS") \
  $([ "$INCLUDE_RAW_OUTPUT" = "true" ] && echo "--include_raw_output") \
  $([ "$FORCE" = "true" ] && echo "--force")

echo "C5 Agent scoring (dspy_opt_prompt method) complete!"
echo ""

fi  # End of agent steps conditional

# ============================================================
# Summary
# ============================================================
echo "=========================================="
echo "ALL SCORING COMPLETE!"
echo "=========================================="
echo "Output directory: $OUTPUT_ROOT/dataset=$DATASET_ID/"
echo ""
echo "Completed methods:"
if [[ "$RUN_MODE" == "prompt_only" ]] || [[ "$RUN_MODE" == "all" ]]; then
  echo "  1. Prompt (noRAG)"
  echo "  2. Prompt (Oracle)"
fi
if [[ "$RUN_MODE" == "agent_only" ]] || [[ "$RUN_MODE" == "all" ]]; then
  echo "  3. Agent (agent method) - C1-C4, C6-C7"
  # echo "  4. Agent (dspy_noopt method) - C1-C4, C6-C7 (SKIPPED)"
  echo "  5. Agent (dspy_opt_prompt method) - C1-C4, C6-C7"
  echo "  6. C5 Agent (agent method)"
  echo "  7. C5 Agent (dspy_opt_prompt method)"
fi
echo "=========================================="
