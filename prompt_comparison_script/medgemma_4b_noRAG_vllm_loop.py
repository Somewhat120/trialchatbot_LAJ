#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NO-RAG LAJ runner (MedGemma 1.5 4B IT, local)
- Reads a CSV (trial_num/question_num/question/answer)
- Loops over prompt variations + prompt JSON files
- Runs MedGemma locally (no HF token; local_files_only=True)
- Uses official-style messages + processor.apply_chat_template
- Saves per-variation outputs into:
    LAJ_score_MEDGEMMA15_4B_baseline/
    LAJ_score_MEDGEMMA15_4B_def_only/
    LAJ_score_MEDGEMMA15_4B_def_cot/

Assumptions:
- Prompt JSON contains:
    - "system_role" (string; may be empty)
    - "instruction_template" with {question}, {answer} (and optionally {context})
- Model is already downloaded to: models/medgemma-1.5-4b-it
"""

import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

import re
import json
import glob
import argparse
from typing import Any, Dict, List, Optional

import pandas as pd
import torch
from tqdm import tqdm
from transformers import AutoProcessor, AutoModelForImageTextToText


# =========================
# CONFIG
# =========================
MODEL_DIR = "models/medgemma-1.5-4b-it"

INPUT_CSV = "data_preparation/raters.csv"

# Match your existing naming style (variation -> output folder)
PROMPT_VARIATIONS = {
    "baseline": {
        "dir": "prompt_comparison_folder/baseline/",
        "output": "LAJ_score_MEDGEMMA15_4B_baseline/",
    },
    "def_only": {
        "dir": "prompt_comparison_folder/prompts_def_only/",
        "output": "LAJ_score_MEDGEMMA15_4B_def_only/",
    },
    "def_cot": {
        "dir": "prompt_comparison_folder/prompts_cot/",
        "output": "LAJ_score_MEDGEMMA15_4B_def_cot/",
    },
}

EXCLUDED_PROMPTS = []  # e.g. ["5_acc_prompt.json"]

# NO-RAG control context
NO_RAG_CONTEXT = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

# Generation
MAX_NEW_TOKENS_DEFAULT = 768  # keep <= ~8192 output limit; 1024 is a safe start
DO_SAMPLE_DEFAULT = False      # deterministic-ish
TEMPERATURE_DEFAULT = 0.0
TOP_P_DEFAULT = 1.0


# =========================
# UTILS
# =========================
def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def discover_prompts(prompts_dir: str, excluded_list: List[str]) -> List[str]:
    if not os.path.exists(prompts_dir):
        return []
    files = sorted(glob.glob(os.path.join(prompts_dir, "*.json")))
    ex = set(excluded_list or [])
    return [f for f in files if os.path.basename(f) not in ex]

def load_prompt(prompt_file: str) -> Optional[dict]:
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading prompt {prompt_file}: {e}")
        return None

def render_instruction_template(prompt_data: dict, context: str, q: str, a: str) -> str:
    """
    Keep this robust but simple:
    - If template has {context}, fill it; otherwise ignore context.
    """
    template = safe_str(prompt_data.get("instruction_template", "")).strip()
    if not template:
        # fallback
        return f"Context:\n{context}\n\nQuestion:\n{q}\n\nAnswer:\n{a}\n\nReturn JSON with final_score 1-5."

    # Escape braces safely (same approach you used elsewhere)
    t = template
    t = t.replace("{context}", "___CTX___").replace("{question}", "___Q___").replace("{answer}", "___A___")
    t = t.replace("{", "{{").replace("}", "}}")
    t = t.replace("___CTX___", "{context}").replace("___Q___", "{question}").replace("___A___", "{answer}")

    try:
        return t.format(context=context, question=q, answer=a).strip()
    except Exception:
        # Try without context
        try:
            return t.format(question=q, answer=a).strip()
        except Exception:
            return f"Context:\n{context}\n\nQuestion:\n{q}\n\nAnswer:\n{a}\n\nReturn JSON with final_score 1-5."

_FINAL_SCORE_RE = re.compile(r'final_score[^0-9]{0,20}(\d)', flags=re.IGNORECASE)

def extract_score(text: str) -> int:
    """
    Parse {"final_score": N} if possible, else regex fallback.
    """
    s = safe_str(text).strip()
    if not s:
        return -1

    # Try JSON substring parse
    try:
        l = s.find("{")
        r = s.rfind("}")
        if l != -1 and r != -1 and r > l:
            obj = json.loads(s[l:r+1])
            if isinstance(obj, dict) and "final_score" in obj:
                return int(obj["final_score"])
    except Exception:
        pass

    m = _FINAL_SCORE_RE.search(s)
    return int(m.group(1)) if m else -1


# =========================
# MESSAGE BUILDING (official-style)
# =========================
def build_messages_medgemma(prompt_data: dict, q: str, a: str, context: str) -> List[Dict[str, Any]]:
    """
    Keep close to the official example:
      messages = [{"role":"user","content":[{"type":"text","text": "..."}]}]
    If you want system_role, we merge it into the user text (stable across servers/templates).
    """
    system_role = safe_str(prompt_data.get("system_role", "")).strip()
    user_text = render_instruction_template(prompt_data, context=context, q=q, a=a)

    if system_role:
        combined = f"{system_role}\n\n{user_text}".strip()
    else:
        combined = user_text

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": combined}
            ],
        }
    ]
    return messages


# =========================
# CORE INFERENCE
# =========================
@torch.inference_mode()
def medgemma_generate(
    model,
    processor,
    messages: List[Dict[str, Any]],
    max_new_tokens: int,
    do_sample: bool,
    temperature: float,
    top_p: float,
) -> str:
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    input_len = inputs["input_ids"].shape[-1]

    gen_kwargs = dict(
        max_new_tokens=max_new_tokens,
        do_sample=do_sample,
    )
    if do_sample:
        gen_kwargs["temperature"] = temperature
        gen_kwargs["top_p"] = top_p

    generation = model.generate(**inputs, **gen_kwargs)
    generation = generation[0][input_len:]

    # MedGemma uses processor.decode
    return processor.decode(generation, skip_special_tokens=True).strip()


# =========================
# CLI + MAIN
# =========================
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input_csv", type=str, default=INPUT_CSV)
    p.add_argument("--model_dir", type=str, default=MODEL_DIR)
    p.add_argument("--max_new_tokens", type=int, default=MAX_NEW_TOKENS_DEFAULT)
    p.add_argument("--do_sample", action="store_true", default=DO_SAMPLE_DEFAULT)
    p.add_argument("--temperature", type=float, default=TEMPERATURE_DEFAULT)
    p.add_argument("--top_p", type=float, default=TOP_P_DEFAULT)
    p.add_argument("--test_n", type=int, default=0, help="If >0, run only first N rows.")
    p.add_argument("--force", action="store_true", help="Overwrite existing output JSON.")
    return p.parse_args()

def main():
    args = parse_args()

    # Load CSV once
    df = pd.read_csv(args.input_csv)
    if args.test_n and args.test_n > 0:
        df = df.head(args.test_n)

    # Load model locally (no tokens; no downloads)
    print(f"⏳ Loading MedGemma from local dir: {args.model_dir}")
    processor = AutoProcessor.from_pretrained(args.model_dir, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(
        args.model_dir,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=True,
    )
    model.eval()
    print("✅ Model loaded.")

    for variation_name, cfg in PROMPT_VARIATIONS.items():
        prompt_dir = cfg["dir"]
        out_dir = cfg["output"]
        ensure_dir(out_dir)

        prompt_files = discover_prompts(prompt_dir, EXCLUDED_PROMPTS)
        if not prompt_files:
            print(f"⚠️ No prompts found for {variation_name}: {prompt_dir}")
            continue

        print("\n" + "=" * 80)
        print(f"VARIATION: {variation_name} | prompts={prompt_dir} | out={out_dir}")
        print("=" * 80)

        for prompt_file in prompt_files:
            prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
            out_path = os.path.join(
                out_dir,
                f"QA_{prompt_name}_medgemma15_4b_it_{variation_name}_NO_RAG_{os.path.basename(args.input_csv).replace('.csv','')}.json"
            )

            if os.path.exists(out_path) and not args.force:
                print(f"⏩ Skip (exists): {out_path}")
                continue

            prompt_data = load_prompt(prompt_file)
            if prompt_data is None:
                continue

            results = []
            for _, row in tqdm(df.iterrows(), total=len(df), desc=f"{prompt_name}", ncols=100):
                trial = safe_str(row.get("trial_num", row.get("nct_id", "UNKNOWN")))
                q = safe_str(row.get("question", row.get("questions", "")))
                a = safe_str(row.get("answer", row.get("response", "")))

                # question_num normalization
                qn = row.get("question_num", None)
                try:
                    qn = int(float(qn)) if (qn is not None and pd.notna(qn)) else None
                except Exception:
                    qn = None

                if not q or not a:
                    results.append({
                        "status": "error",
                        "trial_num": trial,
                        "question_num": qn,
                        "question": q,
                        "chatbot_answer": a,
                        "retrieved_context": "NO_RAG_CONTROL",
                        "ai_reasoning": "",
                        "ai_score": -1,
                        "error_reason": "empty_question_or_answer",
                        "prompt_file": prompt_name,
                        "prompt_variation": variation_name,
                        "model": "medgemma15_4b_it",
                    })
                    continue

                messages = build_messages_medgemma(
                    prompt_data=prompt_data,
                    q=q,
                    a=a,
                    context=NO_RAG_CONTEXT,
                )

                try:
                    resp_text = medgemma_generate(
                        model=model,
                        processor=processor,
                        messages=messages,
                        max_new_tokens=args.max_new_tokens,
                        do_sample=args.do_sample,
                        temperature=args.temperature,
                        top_p=args.top_p,
                    )
                    ai_score = extract_score(resp_text)
                    results.append({
                        "status": "success",
                        "trial_num": trial,
                        "question_num": qn,
                        "question": q,
                        "chatbot_answer": a,
                        "human_score": row.get("Level of factual accuracy", None),  # optional; keep if present
                        "retrieved_context": "NO_RAG_CONTROL",
                        "ai_reasoning": resp_text,
                        "ai_score": ai_score,
                        "prompt_file": prompt_name,
                        "prompt_variation": variation_name,
                        "model": "medgemma15_4b_it",
                    })
                except Exception as e:
                    results.append({
                        "status": "error",
                        "trial_num": trial,
                        "question_num": qn,
                        "question": q,
                        "chatbot_answer": a,
                        "retrieved_context": "NO_RAG_CONTROL",
                        "ai_reasoning": "",
                        "ai_score": -1,
                        "error_reason": str(e),
                        "prompt_file": prompt_name,
                        "prompt_variation": variation_name,
                        "model": "medgemma15_4b_it",
                    })

            # Save
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"✅ Saved: {out_path} ({len(results)} rows)")

    print("\n✅ Done.")

if __name__ == "__main__":
    main()
