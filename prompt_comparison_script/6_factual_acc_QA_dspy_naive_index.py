import os
import json
import re
import argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import torch
from tqdm import tqdm
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from transformers import AutoTokenizer, AutoModelForCausalLM
from openai import OpenAI

# DSPy imports
try:
    import dspy
    from dspy import Signature, InputField, OutputField
    from dspy.clients.lm import LM
except ImportError:
    print("⚠️  DSPy not installed. Run: pip install dspy-ai")
    raise


# =========================================================
# ARGUMENT PARSER
# =========================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Option 1 refactor: retrieval + MedGemma judge backend (model-agnostic pipeline)."
    )
    parser.add_argument(
        "--output_file",
        type=str,
        default=None,
        help="Path to save the JSON output (optional, will auto-generate if not provided).",
    )
    parser.add_argument(
        "--test_mode",
        action="store_true",
        help="If set, run on a small subset of rows (defaults to config TEST_MODE).",
    )
    parser.add_argument(
        "--test_n",
        type=int,
        default=5,
        help="Number of rows to keep in test mode (default: 10).",
    )
    return parser.parse_args()


# =========================================================
# CONFIG (matches your current behavior)
# =========================================================
TEST_MODE_DEFAULT = False
MODEL_NAME = "models/Qwen3-30B-A3B-Instruct-2507"
PROMPT_FILE = "prompts_v6/5_factual_acc_long.json"
DB_FOLDER = "data_preparation/chroma_db_multiscale"
TRIAL_INDEX_PATH = os.path.join(DB_FOLDER, "available_trials.json")
INPUT_CSV = "data_preparation/QA_v9_verified_1209_filtered_with_voted_score.csv"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"
OUTPUT_DIR = "RAG_score_qwen"

# Locked retrieval policy (per your decision)
ALLOWED_SCALES = ["medium"]
RETRIEVAL_K = 3

# Generation
MAX_NEW_TOKENS = 1024
REPETITION_PENALTY = 1.0
DO_SAMPLE = False

# Optional: keep debug prints for retrieval
RETRIEVAL_DEBUG = True


# =========================================================
# DSPy SIGNATURE
# =========================================================
class ICFAuditSignature(dspy.Signature):
    """
    DSPy signature for LAJ factual accuracy auditing.
    """
    context: str = InputField(desc="ICF evidence text (sole source of truth)")
    question: str = InputField(desc="User question")
    answer: str = InputField(desc="Chatbot answer to evaluate")

    reasoning: str = OutputField(desc="2–5 sentence justification")
    final_score: int = OutputField(desc="Integer score from 1 to 5")


# =========================================================
# DSPy HELPER FUNCTIONS
# =========================================================
def load_instruction_text(prompt_file: str) -> str:
    """Load instruction text from JSON or TXT prompt file."""
    with open(prompt_file, "r", encoding="utf-8") as f:
        if prompt_file.endswith('.json'):
            # JSON format: parse and combine system_role + instruction_template
            data = json.load(f)
            return (
                data["system_role"].strip()
                + "\n\n"
                + data["instruction_template"].strip()
            )
        else:
            # TXT format: read directly as plain text
            return f.read().strip()


def get_all_prompt_files(prompt_dir="prompt_dspy_acc"):
    """Get all .txt prompt files from the directory."""
    prompt_files = []
    if os.path.exists(prompt_dir):
        for filename in sorted(os.listdir(prompt_dir)):
            if filename.endswith('.txt'):
                prompt_files.append(os.path.join(prompt_dir, filename))
    return prompt_files

def generate_output_filename(prompt_file, model_name, scale_suffix):
    """Generate output filename from prompt file name."""
    # Extract model short name
    model_short = os.path.basename(model_name).lower().replace("-instruct-2507", "").replace("-a3b", "")
    # Extract prompt name (e.g., "prompt_dspy_acc/5_acc_long.txt" -> "5_acc_long")
    prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
    # Build filename
    filename = f"QA_acc_naive_index_{model_short}_{scale_suffix}_{prompt_name}_verified_1209_filtered_with_voted_score.json"
    return os.path.join(OUTPUT_DIR, filename)

def configure_dspy_with_vllm():
    """Configure DSPy to use the running vLLM server."""
    # Read from environment or use defaults
    vllm_port = int(os.getenv("VLLM_PORT", "8000"))
    vllm_api_base = os.getenv("VLLM_API_BASE", f"http://localhost:{vllm_port}/v1")
    vllm_model_name = os.getenv("VLLM_MODEL_NAME", "qwen3-30b")
    
    lm = LM(
        model=f"openai/{vllm_model_name}",  # Must match --served-model-name from vLLM server
        api_base=vllm_api_base,
        api_key="local",
        model_type="chat",
        max_tokens=512,
        temperature=0.0,
    )
    dspy.settings.configure(lm=lm)


# =========================================================
# UTILITIES
# =========================================================
def normalize_id(val: Any) -> str:
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s.upper().startswith("NCT"):
        s = "NCT" + s
    return s.upper()


def load_trial_index(path: str) -> set:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Trial index not found: {path}. Did you run the DB build script with trial index persistence?"
        )
    with open(path, "r", encoding="utf-8") as f:
        return set(json.load(f))


def safe_json_from_text(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Extract and parse the first JSON object found between the first '{' and last '}'.
    Returns (parsed_obj_or_None, error_message).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "No JSON object boundaries found in model output."

    candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
        if not isinstance(obj, dict):
            return None, "Parsed JSON is not a JSON object."
        return obj, ""
    except Exception as e:
        return None, f"JSON parse error: {e}"


def validate_judge_output(obj: Dict[str, Any]) -> Tuple[int, str]:
    """
    Validate expected fields:
      - reasoning: str
      - final_score: int in [1..5]
    Returns (final_score or -1, validation_error_message).
    """
    if "final_score" not in obj:
        return -1, "Missing key: final_score"
    score = obj.get("final_score")
    if not isinstance(score, int):
        return -1, f"final_score is not int (got {type(score)})"
    if score < 1 or score > 5:
        return -1, f"final_score out of range: {score}"

    # reasoning is optional for validation but expected
    reasoning = obj.get("reasoning", "")
    if not isinstance(reasoning, str):
        return -1, f"reasoning is not str (got {type(reasoning)})"

    return score, ""


# =========================================================
# RETRIEVAL (pure function)
# =========================================================
def create_vector_db(db_folder: str, embedding_model: str) -> Chroma:
    print(f"⏳ Loading Vector DB → {db_folder}")
    embedding_fn = HuggingFaceEmbeddings(
        model_name=embedding_model,
        model_kwargs={"trust_remote_code": True, "device": "cpu"},
    )
    return Chroma(persist_directory=db_folder, embedding_function=embedding_fn)


def get_evidence(
    db: Chroma,
    trial_id: str,
    query: str,
    allowed_scales: List[str],
    k_value: int,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Returns:
      - context_str: evidence block text or "NO EVIDENCE FOUND." / "RETRIEVAL ERROR: ..."
      - chunk_metadata: list of metadata dicts
    """
    try:
        if len(allowed_scales) == 1:
            filter_dict = {
                "$and": [
                    {"nct_id": {"$eq": trial_id}},
                    {"scale": {"$eq": allowed_scales[0]}},
                ]
            }
        else:
            filter_dict = {
                "$and": [
                    {"nct_id": {"$eq": trial_id}},
                    {"scale": {"$in": allowed_scales}},
                ]
            }

        if RETRIEVAL_DEBUG:
            print(f"🔍 DEBUG: Querying trial={trial_id}, scales={allowed_scales}, filter={filter_dict}")

        docs = db.similarity_search(query, k=k_value, filter=filter_dict)

        if not docs:
            return "NO EVIDENCE FOUND.", []

        context_parts = []
        chunk_metadata = []

        for i, d in enumerate(docs):
            scale = d.metadata.get("scale", "unknown")
            context_parts.append(f"=== [EVIDENCE {i+1} (Type: {scale})] ===\n{d.page_content}")
            chunk_metadata.append(
                {
                    "chunk_index": i + 1,
                    "scale": scale,
                    "semantic_tag": d.metadata.get("semantic_tag", "unknown"),
                    "chunk_length": len(d.page_content),
                }
            )

        return "\n\n".join(context_parts), chunk_metadata

    except Exception as e:
        import traceback

        error_msg = f"RETRIEVAL ERROR: {e}"
        print(f"❌ {error_msg}")
        print(f"   Full traceback:\n{traceback.format_exc()}")
        return error_msg, []


# =========================================================
# JUDGE BACKEND INTERFACE (Option 1 핵심)
# =========================================================
class JudgeBackend:
    """Interface: any model backend must implement judge() and return structured dict."""

    def judge(self, context: str, question: str, answer: str) -> Dict[str, Any]:
        raise NotImplementedError


@dataclass
class MedGemmaJudgeConfig:
    model_name: str
    prompt_file: str
    max_new_tokens: int = MAX_NEW_TOKENS
    repetition_penalty: float = REPETITION_PENALTY
    do_sample: bool = DO_SAMPLE
    torch_dtype: Any = torch.bfloat16


class MedGemmaJudge(JudgeBackend):
    """
    MedGemma backend implementation.
    All model loading, prompt formatting, generation, and JSON parsing live here.
    """

    def __init__(self, cfg: MedGemmaJudgeConfig):
        self.cfg = cfg
        self.prompt_data = self._load_prompt(cfg.prompt_file)
        self.tokenizer, self.model = self._load_model(cfg.model_name, cfg.torch_dtype)

    @staticmethod
    def _load_prompt(prompt_file: str) -> Optional[Dict[str, Any]]:
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                prompt_data = json.load(f)
            print("✅ Prompt template loaded.")
            return prompt_data
        except Exception:
            print("⚠️ No prompt template found. Using fallback.")
            return None

    @staticmethod
    def _load_model(model_name: str, torch_dtype: Any):
        print(f"⏳ Loading MedGemma: {model_name}")
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch_dtype,
            device_map="auto",
        )
        return tokenizer, model

    @staticmethod
    def _fill_template(template: str, context: str, question: str, answer: str) -> str:
        """
        Simple, deterministic placeholder replacement.
        Avoids brace-escaping pitfalls.
        """
        return (
            template.replace("{context}", context)
            .replace("{question}", question)
            .replace("{answer}", answer)
        )

    def _build_messages(self, context: str, question: str, answer: str) -> List[Dict[str, str]]:
        """
        Build chat messages for apply_chat_template.
        Keeps your existing prompt.json format.
        """
        if self.prompt_data and "instruction_template" in self.prompt_data:
            system_role = self.prompt_data.get("system_role", "You are a clinical auditor.")
            template = self.prompt_data["instruction_template"]
            user_text = self._fill_template(template, context=context, question=question, answer=answer)
        else:
            system_role = "You are a clinical auditor."
            user_text = (
                f"Context:\n{context}\n\n"
                f"Question: {question}\n"
                f"Answer: {answer}\n\n"
                "Return STRICT JSON with keys reasoning and final_score (1–5)."
            )

        return [
            {"role": "system", "content": system_role},
            {"role": "user", "content": user_text},
        ]

    def judge(self, context: str, question: str, answer: str) -> Dict[str, Any]:
        """
        Returns a structured dict with at least:
          - final_score (int or -1)
          - reasoning (string, may be empty)
        Also includes raw_output + parse/validation errors for debugging.
        """
        messages = self._build_messages(context, question, answer)

        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = {k: v.to(self.model.device) for k, v in inputs.items()}

        with torch.no_grad():
            output = self.model.generate(
                **inputs,
                max_new_tokens=self.cfg.max_new_tokens,
                do_sample=self.cfg.do_sample,
                repetition_penalty=self.cfg.repetition_penalty,
            )

        raw_text = self.tokenizer.decode(
            output[0][inputs["input_ids"].shape[-1] :],
            skip_special_tokens=True,
        )

        parsed, parse_err = safe_json_from_text(raw_text)
        if parsed is None:
            return {
                "final_score": -1,
                "reasoning": "",
                "raw_output": raw_text,
                "parse_error": parse_err,
                "validation_error": "No JSON parsed.",
            }

        score, val_err = validate_judge_output(parsed)
        if score == -1:
            # Keep whatever the model produced, but mark invalid
            return {
                "final_score": -1,
                "reasoning": str(parsed.get("reasoning", "")),
                "raw_output": raw_text,
                "parsed_json": parsed,
                "parse_error": "",
                "validation_error": val_err,
            }

        # Valid
        return {
            "final_score": score,
            "reasoning": parsed.get("reasoning", ""),
            "raw_output": raw_text,
            "parsed_json": parsed,
            "parse_error": "",
            "validation_error": "",
        }


class DummyJudge(JudgeBackend):
    """
    Dummy judge for architecture testing.
    Always returns score=3 to verify the pipeline works without model calls.
    """
    
    def judge(self, context: str, question: str, answer: str) -> Dict[str, Any]:
        return {
            "final_score": 3,
            "reasoning": "dummy test - architecture validation",
            "raw_output": "dummy",
            "parsed_json": {"reasoning": "dummy", "final_score": 3},
            "parse_error": "",
            "validation_error": "",
        }


@dataclass
class VLLMJudgeConfig:
    base_url: str
    model_name: str
    prompt_file: str
    temperature: float = 0.0
    max_tokens: int = 1024


class VLLMJudge(JudgeBackend):
    """
    vLLM-backed judge using OpenAI-compatible Chat Completions API.
    """

    def __init__(self, cfg: VLLMJudgeConfig):
        self.cfg = cfg
        self.prompt_data = self._load_prompt(cfg.prompt_file)
        self.client = OpenAI(
            base_url=cfg.base_url,
            api_key="EMPTY"  # required but unused for local vLLM
        )

    @staticmethod
    def _load_prompt(prompt_file: str) -> Optional[Dict[str, Any]]:
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                prompt_data = json.load(f)
            print("✅ Prompt template loaded (vLLM).")
            return prompt_data
        except Exception:
            print("⚠️ No prompt template found. Using fallback.")
            return None

    @staticmethod
    def _fill_template(template: str, context: str, question: str, answer: str) -> str:
        return (
            template.replace("{context}", context)
            .replace("{question}", question)
            .replace("{answer}", answer)
        )

    def _build_messages(self, context: str, question: str, answer: str):
        if self.prompt_data and "instruction_template" in self.prompt_data:
            system_role = self.prompt_data.get(
                "system_role",
                "You are an expert Informed Consent Form (ICF) Auditor."
            )
            template = self.prompt_data["instruction_template"]
            user_text = self._fill_template(template, context, question, answer)
        else:
            system_role = "You are an expert Informed Consent Form (ICF) Auditor."
            user_text = (
                f"Context:\n{context}\n\n"
                f"Question: {question}\n"
                f"Answer: {answer}\n\n"
                "Return STRICT JSON with keys reasoning and final_score (1–5)."
            )

        return [
            {"role": "system", "content": system_role},
            {"role": "user", "content": user_text},
        ]

    def judge(self, context: str, question: str, answer: str) -> Dict[str, Any]:
        messages = self._build_messages(context, question, answer)

        try:
            resp = self.client.chat.completions.create(
                model=self.cfg.model_name,
                messages=messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
            )
        except Exception as e:
            return {
                "final_score": -1,
                "reasoning": "",
                "raw_output": "",
                "parsed_json": None,
                "parse_error": f"API error: {e}",
                "validation_error": "vLLM call failed",
            }

        raw_text = resp.choices[0].message.content

        parsed, parse_err = safe_json_from_text(raw_text)
        if parsed is None:
            return {
                "final_score": -1,
                "reasoning": "",
                "raw_output": raw_text,
                "parsed_json": None,
                "parse_error": parse_err,
                "validation_error": "No JSON parsed.",
            }

        score, val_err = validate_judge_output(parsed)
        if score == -1:
            return {
                "final_score": -1,
                "reasoning": str(parsed.get("reasoning", "")),
                "raw_output": raw_text,
                "parsed_json": parsed,
                "parse_error": "",
                "validation_error": val_err,
            }

        return {
            "final_score": score,
            "reasoning": parsed.get("reasoning", ""),
            "raw_output": raw_text,
            "parsed_json": parsed,
            "parse_error": "",
            "validation_error": "",
        }


# =========================================================
# DSPy JUDGE MODULE
# =========================================================
class DSPyJudgeModule(dspy.Module):
    """DSPy-based judge module."""
    
    def __init__(self, instruction_text: str):
        super().__init__()
        self.predict = dspy.Predict(
            ICFAuditSignature,
            instructions=instruction_text
        )

    def forward(self, context: str, question: str, answer: str):
        return self.predict(
            context=context,
            question=question,
            answer=answer
        )


# =========================================================
# MAIN ORCHESTRATION (retrieval → judge → save)
# =========================================================
def process_single_prompt(prompt_file, args, db, allowed_scales, retrieval_k, output_json, available_nct_ids):
    """Process a single prompt file and return results."""
    # Load instruction text for this prompt
    instruction_text = load_instruction_text(prompt_file)
    judge = DSPyJudgeModule(instruction_text)
    print(f"✅ DSPy judge initialized for: {os.path.basename(prompt_file)}")
    
    # Decide test mode
    test_mode = args.test_mode or TEST_MODE_DEFAULT

    # Load CSV
    print("📘 Loading CSV:", INPUT_CSV)
    df = pd.read_csv(INPUT_CSV, encoding="utf-8")

    if test_mode:
        print(f"🔬 TEST MODE: Limiting to first {args.test_n} rows.")
        df = df.head(args.test_n)

    df["normalized_id"] = df["trial_num"].apply(normalize_id)
    initial_count = len(df)
    df = df[df["normalized_id"].isin(available_nct_ids)]
    print(f"📉 Filtered CSV: {initial_count} rows -> {len(df)} VALID rows (overlapping with DB).")

    if len(df) == 0:
        print("❌ CRITICAL ERROR: No overlap found between CSV and Database. Check your data.")
        return []

    results: List[Dict[str, Any]] = []
    skipped_trials = set()
    no_evidence_count = 0
    retrieval_error_count = 0

    print(f"\n🚀 Processing {len(df)} valid questions with Scales={allowed_scales}, K={retrieval_k}...\n")

    for _, row in tqdm(df.iterrows(), total=len(df), ncols=80):
        trial = row["normalized_id"]
        q = str(row["question"])
        a = str(row["answer"])
        human_score = row.get("Level of factual accuracy", None)

        # QA retrieval (per your confirmation)
        qa_query = f"{q} {a}"

        ctx, chunk_info = get_evidence(db, trial, qa_query, allowed_scales, retrieval_k)

        if "NO EVIDENCE" in ctx:
            skipped_trials.add(trial)
            no_evidence_count += 1
            continue
        if "RETRIEVAL ERROR" in ctx:
            print(f"❌ Retrieval failed for trial {trial}: {ctx}")
            skipped_trials.add(trial)
            retrieval_error_count += 1
            continue

        # Count scales retrieved
        scale_counts: Dict[str, int] = {}
        for chunk in chunk_info:
            s = chunk["scale"]
            scale_counts[s] = scale_counts.get(s, 0) + 1

        # Judge call (DSPy - structured output guaranteed)
        try:
            result = judge(context=ctx, question=q, answer=a)
            ai_score = int(result.final_score)
            ai_reasoning = result.reasoning
        except Exception as e:
            print(f"⚠️  DSPy judge error for trial {trial}: {e}")
            ai_score = -1
            ai_reasoning = f"DSPy error: {e}"

        results.append(
            {
                "trial_num": trial,
                "question": q,
                "chatbot_answer": a,
                "human_score": human_score,
                "scales_used": allowed_scales,
                "retrieved_context": ctx,
                "retrieved_chunks_info": chunk_info,
                "retrieved_scale_counts": scale_counts,
                # DSPy judge outputs (no parsing needed - structured output)
                "ai_reasoning": ai_reasoning,
                "ai_score": ai_score,
            }
        )

    # Summary
    print("\n📊 SUMMARY:")
    print(f"   ✅ Successfully processed: {len(results)} rows")
    print(f"   ⚠️  No evidence found: {no_evidence_count} rows")
    print(f"   ❌ Retrieval errors: {retrieval_error_count} rows")
    print(f"   📝 Total skipped: {len(skipped_trials)} unique trials")

    if len(results) == 0:
        print("\n⚠️  WARNING: No results to save! All rows were skipped.")
        print("   Possible reasons:")
        print("   1. Scale filter doesn't match available scales in DB")
        print("   2. Retrieval K value too restrictive")
        print("   3. Query similarity threshold too high")
        return []

    return results

def main():
    args = parse_arguments()
    
    # Get all prompt files
    prompt_files = get_all_prompt_files("prompt_dspy_acc")
    if not prompt_files:
        print("❌ No prompt files found in prompt_dspy_acc directory.")
        return
    
    print(f"📋 Found {len(prompt_files)} prompt files:")
    for pf in prompt_files:
        print(f"   - {pf}")
    print()
    
    # Load resources once (shared across all prompts)
    db = create_vector_db(DB_FOLDER, EMBEDDING_MODEL)
    available_nct_ids = load_trial_index(TRIAL_INDEX_PATH)
    
    # Configure DSPy once (shared across all prompts)
    configure_dspy_with_vllm()
    
    # Process each prompt file
    successful_outputs = []
    for prompt_file in prompt_files:
        print("\n" + "="*80)
        print(f"📝 Processing: {os.path.basename(prompt_file)}")
        print("="*80)
        
        # Generate output filename
        scale_suffix = "_".join(ALLOWED_SCALES)
        output_json = generate_output_filename(prompt_file, MODEL_NAME, scale_suffix)
        
        # Process this prompt
        results = process_single_prompt(
            prompt_file, args, db, ALLOWED_SCALES, RETRIEVAL_K, output_json, available_nct_ids
        )
        
        # Save results
        if results:
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=4)
            print(f"✅ Saved {len(results)} results to: {output_json}")
            successful_outputs.append(output_json)


if __name__ == "__main__":
    main()
