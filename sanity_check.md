Global Rules

  - Ignore files with _v2 in the filename.
  - Ignore plain dspy (only dspy_opt_prompt counts).
  - Treat c7_clarity and c7_clarity_succinctness as equivalent.
  - Only C5 uses RAG (prompt_* with evidence=stage1_*). Other criteria are no‑context.
  - Required methods: prompt_zeroshot, prompt_fewshot, agent, dspy_opt_prompt.

  Directories to Scan

  - Prompt (no‑context):
      - .../safety/method=prompt_zeroshot/.../model={MODEL}/
      - .../safety/method=prompt_fewshot/.../model={MODEL}/
      - .../reliability/method=prompt_zeroshot/.../model={MODEL}/
      - .../reliability/method=prompt_fewshot/.../model={MODEL}/
      - .../communication_quality/method=prompt_zeroshot/.../model={MODEL}/
      - .../communication_quality/method=prompt_fewshot/.../model={MODEL}/
  - Prompt C5 RAG (still in prompt_zeroshot, prompt_fewshot):
      - .../reliability/method=prompt_zeroshot/.../model={MODEL}/evidence=stage1_*
      - .../reliability/method=prompt_fewshot/.../model={MODEL}/evidence=stage1_*
  - Agent:
      - .../safety/method=agent/.../model={MODEL}/
      - .../reliability/method=agent/.../model={MODEL}/
      - .../communication_quality/method=agent/.../model={MODEL}/
  - DSPy Opt:
      - .../safety/method=dspy_opt_prompt/.../model={MODEL}/
      - .../reliability/method=dspy_opt_prompt/.../model={MODEL}/
      - .../communication_quality/method=dspy_opt_prompt/.../model={MODEL}/
for the safety folder, only have c1-c3, reliabitliy only have c4-c5, the evidence=no_rag_control only have c4, evidence=icdf_chunks or evidence tags., .. only have c5, communication qualtiy folder only have c6-c7
  Expected Files
one different is for this API model, we use oracel for c5, not evdience tab or icf chunks , also check the multiplt runstamps for the same config and cirterion, which is newer and commplet, with fewer errors
  - For each prompt method (zeroshot, fewshot):
      - Variations: baseline, def_only, def_cot
      - Criteria c1–c7 (c7 name unified)
  - For C5 prompt RAG:
      - baseline/def_only/def_cot each present for zeroshot and fewshot.
  - For agent:
      - c1–c7 (c7 name unified), plus C5 agent.
  - For dspy_opt_prompt:
      - c1–c7 (c7 name unified), plus C5 dspy_opt.

  Checks to Report

  - Missing files per method/variation/criterion.
  - "Big errors":
      - parse errors > 0
      - status=error > 0
      - total rows < 800         - ai_score = -1
  - For dspy compiled artifacts:
      - Verify dspy_engine/compiled_artifacts/**/judge_model={MODEL}/**/compile_meta.json exists for c1–c7.
      - Note naming mismatches (e.g., communication_* vs communication_quality_*).