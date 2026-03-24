# Instructions: Add Claude Support and Keep Logic Parallel to OpenAI

## Shared inputs (same for OpenAI and Claude)

| Purpose | Path | Notes |
|--------|------|--------|
| **Test data** | `data_eval/test_set0226.jsonl` | Input JSONL for scoring. |
| **ICF text (C5 only)** | `ICF_text_cleaned/` | Full ICF text for Oracle / C5 factual accuracy. |

Use the same paths in both OpenAI and Claude flows so outputs stay comparable.

---

## 1. Prompt-based scoring (no RAG + Oracle)

**Goal:** Add Claude versions of the prompt runners and one top-level bash that mirrors the OpenAI one.

### 1.1 Python runners (reference → Claude version)

| Role | OpenAI script | Claude script to add/revise |
|------|----------------|----------------------------|
| No-context (no RAG) | `prompt_comparison_script/02_LAJ_noRAG_loop_gpt.py` | Add `02_LAJ_noRAG_loop_claude.py` (or extend one script with a backend flag). |
| Oracle (with ICF text) | `prompt_comparison_script/02_LAJ_oracle_loop_gpt.py` | Add `02_LAJ_oracle_loop_claude.py` (or same backend flag). |

**What to keep the same (parallel logic):**

- **CLI:** Same arguments (e.g. `--input_jsonl`, `--dataset_id`, `--run_stamp`, `--max_tokens`, `--workers`, `--zeroshot_only` / `--fewshot_only`, `--prompt_variation`, `--prompt_base`, `--force`, `--include_raw_output`). For Oracle, keep `--text_folder`.
- **Output path structure:**  
  `{OUTPUT_ROOT}/dataset={dataset_id}/{domain}/method=prompt_{zeroshot|fewshot}/backend=openai|anthropic/model={model}/evidence={no_context|oracle}/variation={...}/run={stamp}/`  
  Only the `backend=` segment should differ (`openai` vs `anthropic`); segment order and naming must match.
- **Output file naming:** Same pattern (e.g. `{criterion_name}_{version}.jsonl`).
- **Per-row output schema:** Same fields (e.g. `reasoning`, `final_score`, metadata) so downstream eval stays identical.

**Implementation:** Either (a) copy the GPT scripts and swap the API client (Anthropic Messages API) and `backend=anthropic`, or (b) add a single backend parameter and branch on it so one script handles both.

### 1.2 Bash (reference → Claude version)

| Role | OpenAI bash | Claude bash to add/revise |
|------|--------------|---------------------------|
| Full pipeline (prompt noRAG + Oracle) | `run_command/run_all_prompt_openai.sh` | Add `run_command/run_all_prompt_claude.sh` |

**Keep parallel:**

- Same sections: CONFIGURATION, Preflight checks, STEP 1 (Prompt noRAG), STEP 2 (Prompt Oracle), Summary.
- Same env vars where possible: `INPUT_JSONL`, `DATASET_ID`, `SKILLS_ROOT`, `OUTPUT_ROOT`, `RUN_STAMP`, `RUN_MODE`, `PROMPT_VARIATION`, `PROMPT_BASE`, `PROMPT_METHOD`, `FORCE`, `INCLUDE_RAW_OUTPUT`, `WORKERS`.  
  Only change: key file (e.g. `KEY_FILE="prompt_comparison_script/anthropic_key.txt"`) and model (e.g. `JUDGE_MODEL` for Claude).
- Same preflight checks (input file, key file, skills root, `TEXT_FOLDER` for ICF).
- Same `run_prompt_step` usage: call the Claude Python script with the same positional/optional args as in the OpenAI script.
- For Oracle step, pass the same `--text_folder` (e.g. `ICF_text_cleaned`).

Result: one bash for OpenAI, one for Claude; same steps, same layout, only backend and key/model differ.

---

## 2. DSPy COPRO rewriter (skill compile)

**Goal:** Add Claude-backed compile scripts so skill compilation can run on Claude with the same interface and artifact layout as OpenAI.

### 2.1 Python runners (reference → Claude version)

| Role | OpenAI script | Claude script to add/revise |
|------|----------------|----------------------------|
| C1–C4, C6–C7 (single skill) | `dspy_engine/compile/compile_dspy_copro_skill_openai.py` | Add `compile_dspy_copro_skill_claude.py` (or add backend flag). |
| C5 factual accuracy | `dspy_engine/compile/compile_dspy_copro_skill_c5_openai.py` | Add `compile_dspy_copro_skill_c5_claude.py` (or backend flag). |

**Keep parallel:**

- Same CLI (e.g. `--criterion_id`, `--skill_md`, `--dev_jsonl`, `--label_key`, `--judge_model`, `--key_file`, `--compiled_root`, `--depth`, `--num_candidates`, `--text_folder` for C5, etc.).
- Same compiled artifact path pattern under `compiled_root`, e.g.  
  `criterion={criterion_id}/backend=openai|anthropic/judge_model={model}/...`  
  Only `backend=` changes for Claude.
- Same artifact contents (e.g. `best_skill.md`, `compile_meta.json`, `candidates.jsonl`).

### 2.2 Bash (reference → Claude version)

| Role | OpenAI bash | Claude bash to add/revise |
|------|-------------|---------------------------|
| C5 compile | `dspy_engine/compile/run_compile_c5_factual_acc_openai.sh` | Add `run_compile_c5_factual_acc_claude.sh`. |
| Smoke test (one skill) | `dspy_engine/compile/smoke_test_copro_skill_openai.sh` | Add `smoke_test_copro_skill_claude.sh`. |
| All skills (C1–C4, C6–C7) | `bash_noRAG_agent/dspy_skill_compile/run_compile_copro_openai.sh` | Add `run_compile_copro_claude.sh`. |

**Keep parallel:**

- Same CONFIGURATION / env vars (where applicable), same preflight checks, same MODE (test/full) and limits.
- Same Python invocation pattern: same script path pattern (only `_openai` → `_claude` or backend arg), same arguments in the same order.
- Same “Usage examples” / “Complete!” messaging style; only backend name and key file differ.

If `run_compile_copro_openai.sh` delegates to a shared `run_compile_all_skills.sh`, add a Claude entry point (e.g. `run_compile_all_skills_claude.sh` or a backend argument) so `run_compile_copro_claude.sh` mirrors `run_compile_copro_openai.sh` in structure.

---

## 3. Agent skill scoring (loop over skills)

**Goal:** Claude agent runners already exist; ensure their path structure and bash layout match the OpenAI side.

### 3.1 Python runners (reference ↔ existing Claude)

| Role | OpenAI script | Claude script (existing) |
|------|----------------|---------------------------|
| C1–C4, C6–C7 | `agent/run_skill_scoring_loop_openai.py` | `agent/run_skill_scoring_loop_claude.py` |
| C5 (full-text Oracle) | `agent/run_skill_scoring_acc_openai.py` | Add `agent/run_skill_scoring_acc_claude.py` (or extend existing with backend). |

**Path structure (must match):**

- Pattern:  
  `{output_root}/dataset={dataset_id}/{domain}/method={method}/backend=openai|anthropic/model={model}/evidence=no_rag_control/variation={method}/run={stamp}/`
- Same segments in the same order; only `backend=` is `openai` vs `anthropic`.
- Same output filenames (e.g. `{skill_dir_name}_{version}.jsonl`).

**Checklist for Claude agent scripts:**

- Same CLI args as the OpenAI counterpart (`--input_jsonl`, `--dataset_id`, `--skills_root` or `--skills_dir`, `--judge_model`, `--key_file`, `--method`, `--output_root`, `--max_tokens`, `--run_stamp`, `--text_folder` for C5, `--compiled_artifacts_root` / `--copro_*` for dspy_opt_prompt, etc.).
- Same `out_dir` construction as above; only the literal `"backend=openai"` vs `"backend=anthropic"` differs.
- Same per-row output schema so downstream eval is identical.

### 3.2 Bash (reference → Claude version)

| Role | OpenAI bash | Claude bash to add/revise |
|------|-------------|---------------------------|
| C5-only agent | `run_command/run_c5_agent_openai.sh` | Add `run_command/run_c5_agent_claude.sh`. |
| C1–C4, C6–C7 agent (single script) | `bash_noRAG_agent/openai_run_agent.sh` | `bash_noRAG_agent/claude_run_agent.sh` (already exists; align if needed). |
| Full pipeline (prompt + agent) | `run_command/run_all_prompt_openai.sh` (includes agent steps) | Add `run_all_prompt_claude.sh` that runs Claude prompt + Claude agent in the same step order. |

**Keep parallel:**

- Same CONFIGURATION block and env vars (input, dataset_id, skills root/dir, output root, key file, model, method, mode, token limits, force, include_raw_output). For Claude, swap key file and model only.
- Same preflight checks (input file, key file, skills dir, and for C5: text folder).
- Same step order: e.g. Step 3 agent (C1–C4, C6–C7), Step 5 dspy_opt_prompt (C1–C4, C6–C7), Step 6 C5 agent, Step 7 C5 dspy_opt_prompt.
- Same Python command pattern: same args in the same order; only script name and key/model differ.
- Same summary block (e.g. “Completed methods: …”) so the two bash files read side-by-side.

---

## 4. Output path and bash alignment summary

**Path structure (all runners):**

- Prompt:  
  `{OUTPUT_ROOT}/dataset={id}/{domain}/method=prompt_{zeroshot|fewshot}/backend=openai|anthropic/model={model}/evidence={...}/variation={...}/run={stamp}/`
- Agent:  
  `{OUTPUT_ROOT}/dataset={id}/{domain}/method={method}/backend=openai|anthropic/model={model}/evidence=no_rag_control/variation={method}/run={stamp}/`
- Compile:  
  `{COMPILED_ROOT}/criterion={id}/backend=openai|anthropic/judge_model={model}/...`

**Bash alignment:**

- One “family” per backend: `run_all_prompt_openai.sh` ↔ `run_all_prompt_claude.sh`, `openai_run_agent.sh` ↔ `claude_run_agent.sh`, `run_c5_agent_openai.sh` ↔ `run_c5_agent_claude.sh`, and the compile bash scripts as above.
- For each pair: same sections (CONFIGURATION, Preflight, Steps, Summary), same env vars and flags, same order of steps and same arguments to the Python scripts. Only backend-specific pieces (key file, model name, script name or backend flag) differ.

This keeps test data and ICF paths shared, revision targets explicit, and output layout and bash logic parallel between OpenAI and Claude.