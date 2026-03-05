#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$WORKSPACE_ROOT"

# Add workspace root to PYTHONPATH so dspy_engine can be imported
export PYTHONPATH="$WORKSPACE_ROOT:${PYTHONPATH:-}"

# ============================================================
# CONFIGURATION (override via env vars if desired)
# ============================================================
# Same parameters as the vLLM compile scripts
export JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.2}"
export KEY_FILE="${KEY_FILE:-prompt_comparison_script/gpt_key.txt}"
export COMPILED_ROOT="${COMPILED_ROOT:-dspy_engine/compiled_artifacts}"
export DEPTH="${DEPTH:-1}"
export NUM_CANDIDATES="${NUM_CANDIDATES:-3}"

# ============================================================
# Preflight checks
# ============================================================
if [[ ! -f "$KEY_FILE" ]]; then
  echo "ERROR: Missing API key file: $KEY_FILE"
  exit 1
fi

# ============================================================
# Print run config
# ============================================================
echo "=========================================="
echo "OpenAI COPRO Skill Content Compile (Option 1)"
echo "  Rewrites SKILL.md content via COPRO loop"
echo "=========================================="
echo "Judge model:     $JUDGE_MODEL"
echo "Key file:        $KEY_FILE"
echo "Compiled root:   $COMPILED_ROOT"
echo "Depth:           $DEPTH"
echo "Candidates:      $NUM_CANDIDATES"
echo "max_skill_tokens:          2048  (matches vLLM)"
echo "max_judge_output_tokens:   2048  (matches vLLM)"
echo "=========================================="
echo ""
echo "Usage examples:"
echo "  bash $0                    # compile all 7 skills"
echo "  bash $0 --one 1            # compile skill #1 only"
echo "  bash $0 --one safety_c1_critical_harm:v1"
echo ""

# ============================================================
# Run — delegates to the shared compile-all wrapper
# ============================================================
bash dspy_engine/compile/run_compile_all_skills.sh "$@"

echo ""
echo "=========================================="
echo "Compile complete!"
echo "Artifacts: $COMPILED_ROOT/criterion=.../backend=openai/judge_model=$JUDGE_MODEL/..."
echo "=========================================="
