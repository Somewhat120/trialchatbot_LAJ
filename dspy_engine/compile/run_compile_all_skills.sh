#!/usr/bin/env bash
# =============================================================================
# Run COPRO skill compile for all criteria and all skill versions.
#
# Usage (from LAJ project root):
#   bash dspy_engine/compile/run_compile_all_skills.sh
#   bash dspy_engine/compile/run_compile_all_skills.sh --one 1
#   bash dspy_engine/compile/run_compile_all_skills.sh --one safety_c1_critical_harm:v1
#
# Environment (optional):
#   JUDGE_MODEL     default: gpt-5-nano
#   KEY_FILE        default: prompt_comparison_script/gpt_key.txt
#   COMPILED_ROOT   default: dspy_engine/compiled_artifacts
#   DEPTH           default: 1  (use 2 for Likert if desired)
#   NUM_CANDIDATES  default: 3
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/compile_skill_all_config.txt"
COMPILE_SCRIPT="${SCRIPT_DIR}/compile_dspy_copro_skill_openai.py"
LAJ_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

JUDGE_MODEL="${JUDGE_MODEL:-gpt-5-nano}"
KEY_FILE="${KEY_FILE:-prompt_comparison_script/gpt_key.txt}"
COMPILED_ROOT="${COMPILED_ROOT:-dspy_engine/compiled_artifacts}"
DEPTH="${DEPTH:-1}"
NUM_CANDIDATES="${NUM_CANDIDATES:-3}"

run_one() {
  local criterion_id="$1"
  local skill_md="$2"
  local dev_jsonl="$3"
  local label_key="$4"
  echo ""
  echo "========================================"
  echo "COMPILE: $criterion_id | $skill_md"
  echo "========================================"
  python3 "$COMPILE_SCRIPT" \
    --criterion_id "$criterion_id" \
    --skill_md "$LAJ_ROOT/$skill_md" \
    --dev_jsonl "$LAJ_ROOT/$dev_jsonl" \
    --label_key "$label_key" \
    --judge_model "$JUDGE_MODEL" \
    --key_file "$LAJ_ROOT/$KEY_FILE" \
    --compiled_root "$LAJ_ROOT/$COMPILED_ROOT" \
    --depth "$DEPTH" \
    --num_candidates "$NUM_CANDIDATES" \
    --max_skill_tokens 2048 \
    --max_judge_output_tokens 2048
}

# Parse --one <N> or --one criterion_id:version
RUN_ONE=""
if [[ "${1:-}" == "--one" && -n "${2:-}" ]]; then
  RUN_ONE="$2"
  shift 2
fi

cd "$LAJ_ROOT"
if [[ ! -f "$COMPILE_SCRIPT" ]]; then
  echo "Error: Compile script not found: $COMPILE_SCRIPT"
  exit 1
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "Error: Config not found: $CONFIG"
  exit 1
fi

echo "Config: $CONFIG"
echo "Judge model: $JUDGE_MODEL | depth: $DEPTH | num_candidates: $NUM_CANDIDATES"
echo "Compiled root: $LAJ_ROOT/$COMPILED_ROOT"

job=0
while IFS=$'\t' read -r criterion_id skill_md dev_jsonl label_key; do
  # Strip CR from any field (config may have CRLF line endings)
  criterion_id="${criterion_id%$'\r'}"
  skill_md="${skill_md%$'\r'}"
  dev_jsonl="${dev_jsonl%$'\r'}"
  label_key="${label_key%$'\r'}"
  # Skip comments and empty lines
  [[ "$criterion_id" =~ ^#.*$ || -z "$criterion_id" ]] && continue
  job=$((job + 1))
  [ -f "$LAJ_ROOT/$skill_md" ] || { echo "Skipping (missing): $skill_md"; continue; }

  if [[ -n "$RUN_ONE" ]]; then
    if [[ "$RUN_ONE" == "$job" ]]; then
      run_one "$criterion_id" "$skill_md" "$dev_jsonl" "$label_key"
      exit 0
    fi
    # Match criterion_id:version (e.g. safety_c1_critical_harm:v1)
    if [[ "$RUN_ONE" == *:* ]]; then
      want_cid="${RUN_ONE%%:*}"
      want_ver="${RUN_ONE##*:}"
      if [[ "$criterion_id" == "$want_cid" ]]; then
        # Check if this row's skill_md matches version (v1 -> SKILL_v1.md, default -> SKILL.md)
        if [[ "$want_ver" == "default" && "$skill_md" == *"SKILL.md" ]]; then
          run_one "$criterion_id" "$skill_md" "$dev_jsonl" "$label_key"
          exit 0
        fi
        if [[ "$skill_md" == *"SKILL_${want_ver}.md" ]]; then
          run_one "$criterion_id" "$skill_md" "$dev_jsonl" "$label_key"
          exit 0
        fi
      fi
    fi
    continue
  fi

  run_one "$criterion_id" "$skill_md" "$dev_jsonl" "$label_key"
done < "$CONFIG"

echo ""
echo "All jobs completed."
