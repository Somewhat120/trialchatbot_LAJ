# ICON-Bench

ICON-Bench is an evaluation framework for large language model (LLM) chatbots in clinical trial informed consent question answering, comprising a stakeholder-informed rubric, 800 QA pairs with clinician reference ratings spanning trial-specific, general trial knowledge, and mixed questions, and investigates the use of LLM judges for scalable oversight of patient-facing chatbot responses.

## Study Overview

![Figure 1. ICON-Bench workflow](fig1.png)

*Figure 1. Study overview.*

Figure 1 illustrates the ICON-Bench pipeline, spanning dataset construction from oncology clinical trial informed consent forms, rubric development with stakeholder input, and LLM judge implementation and evaluation.

## Overall LLM Judge Performance

![Figure 2. Overall LLM judge performance](fig2.png)

*Figure 2. Overall LLM judge performance across the seven evaluation criteria under each model's best overall configuration.*

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

The study compares multiple judge configurations, including:

- Baseline prompts (criterion name and score range only)
- Definition-only prompts (criterion definition and basic scoring rules)
- Rubric-aligned stepwise prompts (step-by-step scoring decision procedures)
- Human-authored criterion-specific judge skills
- LLM-adapted judge skills from human-authored skills through DSPy

## Model Families

We evaluated models spanning three categories:

- General-purpose and medical-purpose open-weight models, including Qwen, Llama, Gemma, Baichuan, MedGemma, Meditron, and OpenBioLLM variants
- Proprietary frontier models, including GPT-5 family, Gemini-3-Pro, and Claude Sonnet 4.6
- Safety-specialized guard models, used only for safety evaluation

## Factual Accuracy Retrieval Setting

For factual accuracy evaluation, which uniquely requires source text (informed consent form) input for trial-linked questions, open-weight models were evaluated under a retrieval-augmented generation (RAG) setting to accommodate context-length limitations.

## Data Availability

A subset of the evaluation data is provided in `data/` . The full dataset will be released upon acceptance.
