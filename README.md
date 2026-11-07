# ICON-Bench

ICON-Bench (Informed CONsent Benchmark) evaluates large language model (LLM) chatbots in clinical trial informed consent question answering. It combines a stakeholder-informed, seven-criterion rubric with clinician reference ratings for 1,000 question-answer (QA) pairs: 800 oncology and 200 non-oncology. The study compares LLM judges across prompt-based, human-authored agent-skill, and DSPy-optimized implementations.

This repository contains evaluation code, prompts, model profiles, and a 200-record evaluation subset.

## Installation

Use Python 3.12. Dependencies and versions are listed in [requirements.txt](requirements.txt). From the repository root:

```bash
python -m pip install -r requirements.txt
```

## Hardware

API-based evaluation requires internet access and a provider API key. It does not require a local GPU. Open-weight models were served with vLLM on GPU hardware allocated by model size (≤4B: 1×L40, ≤14B: 1×A100, ≤32B: 1×H200, ≤72B: 2×A100).

## Demo

Set `OPENAI_API_KEY` to your OpenAI API key. From the repository root, this command evaluates the first five records for critical harms (C1) using GPT-5.2 and the stepwise zero-shot prompt:

```bash
export OPENAI_API_KEY="your-api-key"
python prompt_evaluation_scripts/02_LAJ_noRAG_loop_gpt.py \
  --input_jsonl data/evaluation_subset.jsonl \
  --dataset_id demo --model gpt-5.2 \
  --prompt_variation def_cot --prompt_base 1_critical_harm \
  --zeroshot_only --test_n 5 --max_tokens 1024
```

Scores and rationales are saved as JSONL files under `LAJ_score_outputs/dataset=demo/`. Change `--model` to `gpt-5-mini` or `gpt-5.4` to use another OpenAI judge. For your own data, replace the `--input_jsonl` path and remove `--test_n 5` to process all records.

## Study Overview

![Figure 1. ICON-Bench workflow](fig1.png)

*Figure 1. Study overview.*

Figure 1 shows dataset construction, stakeholder-informed rubric development, clinician reference rating, and LLM judge evaluation. Source documents came from 50 oncology and 20 non-oncology trials. Five oncology trials were reserved for instruction development; the remaining 45 oncology trials and all 20 non-oncology trials contributed to evaluation. The benchmark includes trial-specific, general trial knowledge, and mixed questions.

## Overall LLM Judge Performance

![Figure 2. Overall LLM judge performance](fig2.png)

*Figure 2. Overall performance of 19 general-purpose and medical-purpose LLM judges. Each model uses the configuration selected by its highest overall score on oncology, applied unchanged to non-oncology. Panel A shows overall scores; panels B and C show criterion-level performance on oncology and non-oncology, respectively.*

The overall score is the unweighted mean of C1-C3 sensitivities and C4-C7 Spearman correlations. Four additional safety-specialized models were evaluated only on C1-C3 and are excluded from this seven-criterion comparison.

## Evaluation Criteria

The released evaluation framework scores chatbot answers across seven criteria:

1. Critical harms
2. Individualized medical advice
3. Inappropriate persuasion
4. Source transparency
5. Factual accuracy
6. Usefulness
7. Clarity and succinctness

## Repository Structure

The repository is organized around the main stages of the study workflow:

- `preprocess/`: Informed consent form preprocessing.
- `dataset_construction/`: QA dataset construction and retrieval index building.
- `evaluation_prompts/`: prompt templates and demonstrations for prompt-based judge evaluation.
- `prompt_evaluation_scripts/`: prompt-based LLM judge scoring workflows.
- `agent_skill_scripts/`: skill-based judge evaluation workflows.
- `judge_skills/`: criterion-specific judge skill files.
- `dspy_engine/`: DSPy-based optimization, inference utilities, and compiled skill artifacts.
- `metrics_evaluation_scripts/`: agreement metrics and summary evaluation tables.
- `score_postprocessing_scripts/`: score normalization and postprocessing.
- `model_configs/` and `output_schemas/`: shared model configurations and structured output schemas.
- `data/`: evaluation data released with the study.

## Judge Configurations

The study compares the following judge configurations:

- Baseline prompts (criterion name, score range, and output format)
- Definition-only prompts (criterion definition and basic scoring rules)
- Rubric-aligned stepwise prompts (step-by-step scoring decision procedures)
- Human-authored criterion-specific judge skills
- LLM-adapted judge skills from human-authored skills through DSPy

Each prompt-based variant supports zero-shot and few-shot evaluation. Configuration selection was performed on oncology; the selected configurations were then applied to non-oncology. Overall and criterion-specific configuration selection are distinct analyses.

## Model Families

The manuscript evaluates 19 general-purpose and medical-purpose judges, plus four safety-specialized judges:

- General-purpose and medical-purpose open-weight models, including Qwen, Llama, Gemma, Baichuan, MedGemma, Meditron, and OpenBioLLM variants
- Proprietary models: GPT-5.4, GPT-5.2, GPT-5-mini, Gemini-3.1-Pro, and Claude-Sonnet-4.6
- Safety-specialized models: GPT-OSS-Safeguard-20B, GPT-OSS-Safeguard-120B, Llama-Guard-2-8B, and Qwen3Guard-Gen-8B, evaluated only on C1-C3

Model profiles are in [model_configs/model_profiles.json](model_configs/model_profiles.json). The older Gemini 3 Pro profile is retained for historical runs; the current Gemini profile is `google_gemini_3_1_pro_generate_content`. Model names and generation arguments must also be selected in the runner being used; adding a profile does not change an existing runner's defaults.

## Safety Model Evaluation

[02_LAJ_safety.py](prompt_evaluation_scripts/02_LAJ_safety.py) is an independent C1-C3 runner. It reads the existing criterion prompts and demonstrations, preserves their scoring rules, and adapts the requested output format where a model uses safety labels. Existing prompt, agent-skill, and DSPy runners are separate.

| Profile | Model ID |
|---|---|
| `gpt_oss_safeguard_20b` | `openai/gpt-oss-safeguard-20b` |
| `gpt_oss_safeguard_120b` | `openai/gpt-oss-safeguard-120b` |
| `llama_guard_2_8b` | `meta-llama/Meta-Llama-Guard-2-8B` |
| `qwen3guard_gen_8b` | `Qwen/Qwen3Guard-Gen-8B` |

From the repository root, inspect a prepared request without loading or calling a model:

```bash
python prompt_evaluation_scripts/02_LAJ_safety.py --model_profile qwen3guard_gen_8b --dry_run
```

The default input is `data/evaluation_subset.jsonl`. Use `--input_jsonl PATH` for another dataset, `--prompt_variation baseline`, `def_only`, or `def_cot` to select the prompt, `--fewshot` to include demonstrations, and `--criterion C1`, `C2`, or `C3` to select one criterion. The default is stepwise zero-shot evaluation of all three criteria.

For inference, remove `--dry_run` and supply `--vllm_api_base URL` and `--model NAME` for the running server. Alternatively, use `--backend transformers --model MODEL_PATH` for local weights. `--test_n 5` limits the run to five input records; `--output_dir PATH` directs results to a chosen folder.

Current defaults are temperature 0, top-p 1, seed 0, and 1,024 output tokens. Local generation disables sampling. GPT-OSS profiles use low reasoning effort. The runner allows two retries with a 2,048-token limit; `--max_retries 3` selects three retries. Effective settings are saved with each result.

Results are JSONL records containing `ai_score`, `ai_reasoning`, and `judge_output_raw`. Native labels are converted to 0 or 1; Qwen's `Controversial` label maps to 1. A missing rationale remains empty. Unreadable or truncated responses are recorded as errors with score -1. Format and call handling have been checked with simulated responses; this does not validate model accuracy or reproduce the manuscript's historical runs.

## Factual Accuracy Retrieval Setting

For factual accuracy evaluation, which uniquely requires source text (informed consent form) input for trial-linked questions, open-weight models were evaluated under a retrieval-augmented generation (RAG) setting to accommodate context-length limitations.

## Data Availability

A 200-record evaluation subset is provided in `data/` in JSONL format, with questions, answers, trial and category metadata, and clinician reference scores. See [data/README.md](data/README.md) for its composition and fields. The full dataset and supporting materials will be released upon publication.
