# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM-as-Judge (LAJ) evaluation pipeline for clinical trial chatbot responses. The system scores chatbot answers across 7 criteria using LLM judges, with multiple backends (OpenAI, Anthropic, Gemini, vLLM).

**Criteria** (C1-C3 binary 0/1; C4 endpoints 1/5; C5-C7 Likert 1-5):
- C1: Critical harms risks | C2: Individualized medical advice | C3: Inappropriate persuasion
- C4: Transparency | C5: Factual accuracy (requires ICF context) | C6: Usefulness | C7: Clarity

## Setup

Python 3.10, managed with `uv`. Dependencies in `pyproject.toml` (anthropic, dspy, tqdm).

```bash
uv sync          # install deps
```

## Running the Pipeline

Each backend has parallel bash scripts with identical structure. Replace `openai` with `claude` or `gemini` in script names below.

```bash
# Full pipeline (prompt + agent scoring)
bash run_command/run_all_prompt_openai.sh      # OpenAI
bash run_command/run_all_prompt_claude.sh       # Anthropic
bash run_command/run_all_prompt_gemini.sh       # Gemini

# Agent-only or prompt-only
RUN_MODE=agent_only bash run_command/run_all_prompt_openai.sh
RUN_MODE=prompt_only bash run_command/run_all_prompt_openai.sh

# Override model/workers
JUDGE_MODEL=gpt-5-mini-2025-08-07 WORKERS=4 bash run_command/run_all_prompt_openai.sh

# DSPy COPRO skill compilation (all criteria except C5)
bash bash_noRAG_agent/dspy_skill_compile/run_compile_copro_openai.sh
bash bash_noRAG_agent/dspy_skill_compile/run_compile_copro_claude.sh

# C5 factual accuracy compilation
bash dspy_engine/compile/run_compile_c5_factual_acc_openai.sh

# Smoke test single skill compile
bash dspy_engine/compile/smoke_test_copro_skill_openai.sh
```

All bash scripts set `PYTHONPATH` to workspace root automatically.

## Architecture

### Scoring Approaches

1. **Prompt-based** (`prompt_comparison_script/`): Direct LLM scoring with prompt variations (baseline, def_only, def_cot) x (zeroshot, fewshot). No skill files used.
2. **Agent-based** (`agent/`): Uses SKILL markdown files as rubrics. Methods: `agent` (raw skill), `dspy_noopt`, `dspy_opt_prompt` (COPRO-rewritten skill).
3. **DSPy compilation** (`dspy_engine/compile/`): COPRO-like optimizer that rewrites SKILL.md text to maximize scoring accuracy on dev data.

### Backend Parallelism

All backends (OpenAI, Anthropic, Gemini) follow strictly parallel structure: same CLI args, same output path layout (only `backend=openai|anthropic|gemini` differs), same output schema. When adding a new backend, mirror the existing OpenAI script and change only the API client, key file, and backend segment. See `instruction.md` for the full alignment spec.

Per-backend script naming convention:
- Prompt runners: `02_LAJ_noRAG_loop_{gpt,claude,gemini}.py`, `02_LAJ_oracle_loop_{gpt,claude,gemini}.py`
- Agent runners: `run_skill_scoring_loop_{openai,claude,gemini}.py`, `run_skill_scoring_acc_{openai,claude,gemini}.py`
- Compile scripts: `compile_dspy_copro_skill_{openai,claude,gemini}.py`
- DSPy LM wrappers: `lm_openai_responses.py`, `lm_anthropic.py` (each wraps the respective API for DSPy)

### Key Directories

- `skills/` — SKILL markdown rubrics: `{domain}/{criterion}/SKILL_v*.md`. Placeholders `{question}`, `{answer}`, `{context}` filled at runtime.
- `prompt_comparison_folder/` — Prompt templates (JSON) for the 3 variations + demonstrations for few-shot.
- `data_eval/` — Test JSONL (`test_set0226.jsonl`). Each row has question/answer pairs with human labels.
- `data_preparation/development/` — Per-criterion dev JSONL files for compilation.
- `ICF_text_cleaned/` — Full ICF (Informed Consent Form) text by cancer type. Used for C5 factual accuracy (oracle context). Files named `{NCT_ID}_{suffix}.txt`.
- `dspy_engine/compiled_artifacts/` — Compiled skill outputs. Path: `criterion={id}/skill_version={v}/backend={b}/judge_model={m}/rewriter_model={r}/optimizer=copro_skill/depth={d}/candidates={n}/run={stamp}/best_skill.md`

### Output Path Convention

All runners write to `LAJ_score_outputs/` with a structured path:
- Prompt: `dataset={id}/{domain}/method=prompt_{zeroshot|fewshot}/backend={b}/model={m}/evidence={e}/variation={v}/run={stamp}/`
- Agent: `dataset={id}/{domain}/method={method}/backend={b}/model={m}/evidence=no_rag_control/variation={method}/run={stamp}/`

Output files are `{criterion_name}_{version}.jsonl` with per-row schema: `reasoning`, `final_score`, plus metadata.

### DSPy Engine

- `dspy_engine/signatures.py` — DSPy 3.x signatures: `Binary01Signature`, `Endpoints15Signature`, `Likert15Signature`
- `dspy_engine/lm_openai_responses.py` — Custom DSPy LM wrapper for OpenAI Responses API
- `dspy_engine/lm_anthropic.py` — Custom DSPy LM wrapper for Anthropic Messages API
- `dspy_engine/utils_paths.py` — `resolve_latest_best_skill_path()` finds the latest compiled `best_skill.md`

### Criterion ID Mapping

Scripts use `SKILL_DIR_TO_CRITERION_ID` to map directory names to full criterion IDs:
- `c1_critical_harm` -> `safety_c1_critical_harm`
- `c5_factual_accuracy` -> `reliability_c5_factual_accuracy`
- `c6_usefulness` -> `communication_c6_usefulness`
- etc.

C5 is handled separately from C1-C4/C6-C7 because it requires ICF document context (`--text_folder`).

## API Keys

Store in `prompt_comparison_script/gpt_key.txt` (OpenAI) or `prompt_comparison_script/anthropic_key.txt` (Anthropic). These are gitignored (`*_key.txt`).
