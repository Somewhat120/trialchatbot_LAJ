#!/bin/bash
# ============================================================
# Smoke test for compile_dspy_copro_skill_gemini.py
#
# Run from the LAJ project root:
#   bash dspy_engine/compile/smoke_test_copro_skill_gemini.sh
#
# This uses --limit 3 to only evaluate 3 dev rows (fast & cheap)
# and --num_candidates 2 to keep the run short.
# If it succeeds, you'll see baseline/best accuracy and an artifact dir.
# ============================================================

set -euo pipefail

echo "========================================"
echo "COPRO Skill Optimization (Gemini) - Smoke Test"
echo "========================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$WORKSPACE_ROOT:${PYTHONPATH:-}"

# Quick test: binary criterion (safety_c1), depth=1, num_candidates=2, limit=3
uv run python dspy_engine/compile/compile_dspy_copro_skill_gemini.py \
  --criterion_id safety_c1_critical_harm \
  --skill_md skills/safety/c1_critical_harm/SKILL_v1.md \
  --dev_jsonl data_preparation/development/1_critical_harm.jsonl \
  --label_key "Presence of Critical Harms Risks" \
  --judge_model "${JUDGE_MODEL:-gemini-3-pro-preview}" \
  --key_file "${KEY_FILE:-prompt_comparison_script/gemini_key.txt}" \
  --compiled_root dspy_engine/compiled_artifacts \
  --depth 1 \
  --num_candidates 2 \
  --max_judge_output_tokens 1024 \
  --limit 3

echo ""
echo "========================================"
echo "Smoke test complete. Check the output above."
echo ""
echo "SUCCESS CRITERIA:"
echo "  1. No Python tracebacks"
echo "  2. Baseline acc and Best acc are printed"
echo "  3. Artifact dir contains:"
echo "     - best_skill.md"
echo "     - compile_meta.json"
echo "     - candidates.jsonl"
echo "========================================"
