# Instructions: Add Gemini Support and Keep Logic Parallel to OpenAI

This document defines how to implement Gemini API support by cloning behavior from the **existing OpenAI scripts** in this repo.

Important context:
- In this workspace, OpenAI scripts are the source of truth.
- Some Claude wrappers referenced in older notes are missing; do not depend on them.
- Goal is experimental comparability: keep data paths, CLI, output schema, path layout, and step order parallel.

---

## 0. Core invariants (must hold)

1. Keep the same input paths:
- `data_eval/test_set0226.jsonl`
- `ICF_text_cleaned/`

2. Keep the same CLI arguments and semantics as OpenAI scripts.

3. Keep the same per-row output schema and field names.

4. Keep the same output path segment order and filenames.

5. Only change backend-specific pieces:
- API client
- model name default
- key file
- `backend=` segment value

Recommended backend tag:
- Use `backend=google` (provider-level naming).
- If you prefer `backend=gemini`, use it consistently everywhere (prompt, agent, compile).

---

## 1. Prompt-based scoring (noRAG + Oracle)

### 1.1 Python runners (OpenAI -> Gemini)

Reference OpenAI scripts:
- `prompt_comparison_script/02_LAJ_noRAG_loop_gpt.py`
- `prompt_comparison_script/02_LAJ_oracle_loop_gpt.py`

Add Gemini scripts:
- `prompt_comparison_script/02_LAJ_noRAG_loop_gemini.py`
- `prompt_comparison_script/02_LAJ_oracle_loop_gemini.py`

Keep parallel:
- Same CLI flags (`--input_jsonl`, `--dataset_id`, `--run_stamp`, `--max_tokens`, `--workers`, `--zeroshot_only`, `--fewshot_only`, `--prompt_variation`, `--prompt_base`, `--force`, `--include_raw_output`, `--max_input_chars`, and `--text_folder` for oracle).
- Same path layout:
  - `{OUTPUT_ROOT}/dataset={dataset_id}/{domain}/method=prompt_{zeroshot|fewshot}/backend={google|gemini}/model={model}/evidence={no_context|oracle_full_text}/variation={...}/run={stamp}/`
- Same output filename pattern and row schema.

Gemini call behavior:
- Send same prompt content as OpenAI runner.
- Request JSON output (`response_mime_type="application/json"`).
- Parse strictly, then fallback to regex extraction for `final_score`.
- Keep retry behavior aligned with OpenAI scripts.

### 1.2 Bash wrapper

Reference:
- `run_command/run_all_prompt_openai.sh`

Add:
- `run_command/run_all_prompt_gemini.sh`

Keep parallel:
- Same section structure: CONFIGURATION, Preflight, STEPs, Summary.
- Same env vars and step order.
- Only swap: key file, model default, script names, backend segment.

Recommended defaults:
- `KEY_FILE="prompt_comparison_script/gemini_key.txt"`
- `JUDGE_MODEL` set via env default and override.

---

## 2. Agent scoring (loop + C5 full-text)

### 2.1 Python runners (OpenAI -> Gemini)

Reference OpenAI scripts:
- `agent/run_skill_scoring_loop_openai.py`
- `agent/run_skill_scoring_acc_openai.py`

Add Gemini scripts:
- `agent/run_skill_scoring_loop_gemini.py`
- `agent/run_skill_scoring_acc_gemini.py`

Keep parallel:
- Same CLI and defaults where possible.
- Same output path structure/order:
  - `{output_root}/dataset={dataset_id}/{domain}/method={method}/backend={google|gemini}/model={model}/evidence={no_rag_control|icf_oracle_fulltext}/variation={method}/run={stamp}/`
- Same resume/dedup logic.
- Same per-row schema.
- Same handling for `dspy_opt_prompt` artifact resolution; only backend label differs.

### 2.2 Bash wrapper

References:
- `run_command/run_c5_agent_openai.sh`
- `run_command/run_all_prompt_openai.sh` (agent steps 3/5/6/7)

Add:
- `run_command/run_c5_agent_gemini.sh`
- include Gemini agent steps in `run_all_prompt_gemini.sh` with same order.

---

## 3. DSPy COPRO compile

### 3.1 Python compile scripts (OpenAI -> Gemini)

Reference OpenAI scripts:
- `dspy_engine/compile/compile_dspy_copro_skill_openai.py`
- `dspy_engine/compile/compile_dspy_copro_skill_c5_openai.py`

Add Gemini scripts:
- `dspy_engine/compile/compile_dspy_copro_skill_gemini.py`
- `dspy_engine/compile/compile_dspy_copro_skill_c5_gemini.py`

Keep parallel:
- Same CLI and optimization loop behavior.
- Same artifact contents (`best_skill.md`, `compile_meta.json`, `candidates.jsonl`).
- Same artifact path pattern; only backend segment differs:
  - `.../criterion={id}/skill_version={v}/backend={google|gemini}/judge_model={model}/rewriter_model={model}/optimizer=copro_skill/depth={d}/candidates={k}/run={stamp}/`

### 3.2 Bash wrappers

References:
- `dspy_engine/compile/run_compile_c5_factual_acc_openai.sh`
- `dspy_engine/dspy_skill_compile/run_compile_copro_openai.sh`
- `dspy_engine/compile/run_compile_all_skills.sh`

Add:
- `dspy_engine/compile/run_compile_c5_factual_acc_gemini.sh`
- `dspy_engine/dspy_skill_compile/run_compile_copro_gemini.sh`
- either:
  - add a Gemini compile-all wrapper, or
  - parameterize `run_compile_all_skills.sh` with backend and compile script path.

Keep parallel:
- Same sections, checks, modes, and command shape.

---

## 4. Gemini API configuration policy for this study

This project evaluates LLM-as-a-judge for unsafe/sensitive content under human rubric. To avoid confounding by provider moderation behavior, use a stable and explicit config policy.

### 4.1 Recommended generation settings

Use for judge calls unless specific script requires otherwise:
- `temperature=0.0`
- `top_p=1.0`
- `max_output_tokens` mapped from existing `--max_tokens`
- JSON mode: `response_mime_type="application/json"`
- Thinking: low or effectively off (choose one and hold constant in all scripts)

Notes:
- Gemini docs commonly show `thinking_budget` control; some examples mention `thinking_level` in newer APIs. Use whichever is supported by the installed SDK/model and keep it fixed globally.
- Avoid streaming in production scoring unless parser is built for chunk concat; non-streaming is simpler and less error-prone.

### 4.2 Features to disable for comparability

Disable/unset:
- Code execution tools
- Search tools
- Media/image/video options (`media_resolution` irrelevant for text-only judging)
- Function/tool calling

### 4.3 Safety settings recommendation

For this benchmark design, set per-category threshold to least blocking available and audit outcomes:
- Harassment
- Hate speech
- Sexually explicit
- Dangerous content
- Civic integrity (if supported by model)

Recommended threshold target:
- `BLOCK_NONE` for each supported category.

Caveat:
- Gemini still enforces non-configurable core safety protections; some prompts may be blocked even with `BLOCK_NONE`.

Operational requirement:
- Log safety metadata in outputs when available (`finish_reason`, safety ratings) to quantify moderation interference.

---

## 5. Suggested validation protocol before full run

1. Smoke test each new Gemini runner with small row limits.
2. Validate output path parity against OpenAI run structure.
3. Validate row schema parity (same keys/types).
4. Measure parse error rate and safety-block rate.
5. Run full pipeline only after parity checks pass.

---

## 6. Minimal Gemini request template (text-only judge)

Use as a baseline pattern (adapt to model/SDK version actually available in environment):

```python
from google import genai
from google.genai import types

client = genai.Client(api_key=api_key)

config = types.GenerateContentConfig(
    temperature=0.0,
    top_p=1.0,
    max_output_tokens=max_tokens,
    response_mime_type="application/json",
    # pick one supported thinking control and keep fixed:
    # thinking_config=types.ThinkingConfig(thinking_budget=0),
    # OR thinking_config=types.ThinkingConfig(thinking_level="LOW"),
    safety_settings=[
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ],
)

resp = client.models.generate_content(
    model=model_name,
    contents=user_text,
    config=config,
)
raw_text = resp.text or ""
```

If you stream, concatenate chunks before JSON parse.

---

## 7. Decisions to lock before implementation

1. Backend tag string: `google` vs `gemini`.
2. Primary judge model name for reproducible runs.
3. Thinking control field to standardize (`thinking_budget` or `thinking_level`) based on installed SDK/model support.
4. Whether to run streaming or non-streaming (recommend non-streaming for stability).
5. Whether to persist safety metadata fields in every output row.

---

## 8. Gemini File Inventory (Implemented in This Repo)

Status legend:
- ✅ present and pushed
- 🔧 present and updated for Gemini

### 8.1 Prompt scoring
- ✅ `prompt_comparison_script/02_LAJ_noRAG_loop_gemini.py`
- ✅ `prompt_comparison_script/02_LAJ_oracle_loop_gemini.py`

### 8.2 Agent scoring
- ✅ `agent/run_skill_scoring_loop_gemini.py`
- ✅ `agent/run_skill_scoring_acc_gemini.py`

### 8.3 Run wrappers and instructions
- ✅ `run_command/run_all_prompt_gemini.sh`
- ✅ `run_command/run_c5_agent_gemini.sh`
- ✅ `run_command/instruction_gemini.md`

### 8.4 DSPy COPRO compile (Gemini)
- ✅ `dspy_engine/compile/compile_dspy_copro_skill_gemini.py`
- ✅ `dspy_engine/compile/compile_dspy_copro_skill_c5_gemini.py`
- ✅ `dspy_engine/compile/run_compile_c5_factual_acc_gemini.sh`
- ✅ `dspy_engine/dspy_skill_compile/run_compile_copro_gemini.sh`
- 🔧 `dspy_engine/compile/run_compile_all_skills.sh` (supports `COMPILE_SCRIPT` override for Gemini compile-all flow)

---

## 9. Git Pull Commands

Use these commands on any clone of this repository to fetch the Gemini updates:

```bash
cd /path/to/trialchatbot_LAJ
git checkout main
git pull origin main
```

Optional verification:

```bash
git log --oneline -n 5
```

Expected Gemini commits include:
- `f0426e6` Add Gemini COPRO compile scripts and wrappers
- `637aa9e` Add remaining Gemini scoring runners and run commands
