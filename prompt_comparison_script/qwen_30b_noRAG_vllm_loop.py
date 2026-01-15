import os
import glob
import json
import pandas as pd
import re
import argparse
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI  # vLLM uses OpenAI-compatible API

# ==========================================
# CONFIGURATION
# ==========================================
# Same structure as the first script to run all experiments at once
PROMPT_VARIATIONS = {
    "baseline": {
        "dir": "prompt_comparison_folder/baseline/",
        "output": "LAJ_score_QWEN_baseline/"
    },
    "def_only": {
        "dir": "prompt_comparison_folder/prompts_def_only/",
        "output": "LAJ_score_QWEN_def_only/"
    },
    "def_cot": {
        "dir": "prompt_comparison_folder/prompts_cot/",
        "output": "LAJ_score_QWEN_def_cot/"
    }
}

EXCLUDED_PROMPTS = [
    "5_acc_prompt.json"]
INPUT_CSV = "data_preparation/QA_v9_verified_0104.csv"

# vLLM Configuration for Qwen3-30B
USE_VLLM = True 
VLLM_API_BASE = os.getenv("VLLM_API_BASE", "http://localhost:8000/v1")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "qwen3-30b")  # Must match --served-model-name from vLLM server 

TEST_MODE = False 
MAX_NEW_TOKENS = 1024
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "32")) 

FILENAME_TO_COLUMN = {
    "1_critical_harm": "Presence of Critical Harms Risks",
    "2_individualized_advice": "Presence of individualized medical advice",
    "3_persuation": "Presence of inappropriately persuasion",
    "4_transparent": "Level of transparency",
    "6_useful": "Level of usefulness",
    "7_clarity": "Level of Clarity and Succinctness"
}

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def discover_prompts(prompts_dir, excluded_list):
    files = glob.glob(os.path.join(prompts_dir, "*.json"))
    filtered = [f for f in files if os.path.basename(f) not in excluded_list]
    filtered.sort()
    return filtered

def get_human_score_column(prompt_name):
    return FILENAME_TO_COLUMN.get(prompt_name, None)

def load_prompt(prompt_file):
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading prompt {prompt_file}: {e}")
        return None

def extract_score(text):
    """Extract score from Qwen response."""
    # Try JSON parsing first
    try:
        json_match = re.search(r'\{[^{}]*"final_score"[^{}]*\}', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            parsed = json.loads(json_str)
            if "final_score" in parsed:
                return int(parsed["final_score"])
    except:
        pass
    
    # Fallback regex
    m = re.search(r'"final_score"\s*[:=]\s*(\d+)', text)
    if m: return int(m.group(1))
    
    m = re.search(r'\bfinal_score\b[^0-9]{0,10}(\d+)', text, flags=re.I)
    if m: return int(m.group(1))
    
    return -1  # Return -1 to indicate parsing failure

def format_messages(prompt_data, context, q, a):
    """Format messages for Qwen chat template."""
    system_role = "You are a clinical auditor."
    user_text = f"Context:\n{context}\n\nQuestion: {q}\nAnswer: {a}\n\nPlease evaluate and provide a score from 1-5."

    if prompt_data:
        system_role = prompt_data.get("system_role", system_role)
        if system_role == "":
            system_role = "You are a clinical auditor."
        
        if "instruction_template" in prompt_data:
            try:
                # Try with context first
                try:
                    user_text = prompt_data["instruction_template"].format(
                        context=context, question=q, answer=a
                    )
                except KeyError:
                    # Fallback for templates without context
                    user_text = prompt_data["instruction_template"].format(
                        question=q, answer=a
                    )
            except KeyError as e:
                print(f"⚠️ Prompt template key error: {e}")
    
    if system_role == "":
        return [{"role": "user", "content": user_text}]
    else:
        return [{"role": "system", "content": system_role}, {"role": "user", "content": user_text}]

def save_atomic(data, filepath):
    dirname = os.path.dirname(filepath)
    if dirname: os.makedirs(dirname, exist_ok=True)
    temp_path = filepath + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.rename(temp_path, filepath)
        return True
    except Exception as e:
        print(f"❌ Error saving {filepath}: {e}")
        return False

# ==========================================
# CONCURRENT PROCESSING
# ==========================================
def process_single_row(row_data):
    idx, row, client, prompt_data, human_score_column, model_name, max_tokens = row_data
    
    # Handle different CSV column names
    # JCO_faq.csv uses: question_num, model, prompt, questions, response
    # Other CSVs might use: trial_num, question, answer
    trial = str(row.get("trial_num", row.get("nct_id", row.get("model", "GENERAL_FAQ"))))
    q = str(row.get("question", row.get("questions", "")))
    a = str(row.get("answer", row.get("response", "")))
    
    # Extract question_num from CSV row (with fallback)
    question_num = row.get("question_num", None)
    if question_num is None:
        question_num = row.get("question_id", None)
    if question_num is not None:
        try:
            question_num = int(float(question_num)) if pd.notna(question_num) else None
        except (ValueError, TypeError):
            question_num = None
    # If still None, use index as last resort
    if question_num is None:
        try:
            question_num = int(idx) + 1
        except (ValueError, TypeError):
            question_num = None
    
    # Skip if question or answer is empty
    if not q or not a:
        return {
            "status": "error",
            "reason": "empty_question_or_answer",
            "trial_num": trial,
            "question_num": question_num,
            "question": q,
            "chatbot_answer": a,
            "human_score": None,
            "retrieved_context": "NO_RAG_CONTROL",
            "ai_reasoning": "Empty question or answer",
            "ai_score": -1
        }
    
    human_score = row.get(human_score_column, None) if human_score_column else None

    # DUMMY CONTEXT (NO RAG)
    ctx = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

    messages = format_messages(prompt_data, ctx, q, a)
    
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0,
            top_p=1.0,          # Ensures deterministic sampling
            max_tokens=max_tokens,
            seed=42,             # Set random seed for reproducibility
        )
        resp_text = resp.choices[0].message.content
        ai_score = extract_score(resp_text)
    except Exception as e:
        return {
            "status": "error", "reason": str(e), "trial_num": trial,
            "question_num": question_num,
            "question": q, "chatbot_answer": a, "human_score": human_score,
            "ai_reasoning": f"Error: {str(e)}", "ai_score": -1
        }
    
    return {
        "status": "success", "trial_num": trial, "question_num": question_num,
        "question": q, "chatbot_answer": a, "human_score": human_score,
        "retrieved_context": "NO_RAG_CONTROL", "ai_reasoning": resp_text,
        "ai_score": ai_score
    }

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    parser.add_argument("--max_workers", type=int, default=None)
    args = parser.parse_args()

    # 1. Load Data
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    if TEST_MODE:
        print("🔬 TEST MODE ON: Processing only first 5 rows.")
        df = df.head(5)

    # 2. Setup Client
    print(f"🤖 CONNECTING TO VLLM SERVER: {VLLM_API_BASE}")
    print(f"   Model: {VLLM_MODEL_NAME}")
    try:
        client = OpenAI(base_url=VLLM_API_BASE, api_key="EMPTY")
        # Test connection
        models = client.models.list()
        print(f"✅ Connected to vLLM server. Available models: {[m.id for m in models.data]}")
        model_short = VLLM_MODEL_NAME.replace("-", "_").replace(".", "")
    except Exception as e:
        print(f"❌ Failed to connect to vLLM server: {e}")
        print(f"   Make sure vLLM server is running on {VLLM_API_BASE}")
        return

    # 3. OUTER LOOP: PROMPT VARIATIONS
    for variation_name, variation_config in PROMPT_VARIATIONS.items():
        PROMPTS_DIR = variation_config["dir"]
        OUTPUT_DIR = variation_config["output"]

        print(f"\n" + "="*80)
        print(f"📝 PROCESSING VARIATION: {variation_name.upper()}")
        print(f"   Prompts: {PROMPTS_DIR}")
        print(f"   Output: {OUTPUT_DIR}")
        print("="*80)

        if not os.path.exists(PROMPTS_DIR):
            print(f"⚠️ Prompt directory not found: {PROMPTS_DIR}")
            continue

        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        prompt_files = discover_prompts(PROMPTS_DIR, EXCLUDED_PROMPTS)
        
        # 4. INNER LOOP: PROMPTS
        for prompt_file in prompt_files:
            prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
            output_filename = f"QA_{prompt_name}_{model_short}_{variation_name}_NO_RAG_JCO_FAQ.json"
            output_path = os.path.join(OUTPUT_DIR, output_filename)

            if os.path.exists(output_path) and not args.force:
                print(f"⏩ Skipping {output_filename} (Exists)")
                continue

            print(f"   📝 Running Prompt: {prompt_name}...")
            prompt_data = load_prompt(prompt_file)
            if not prompt_data: continue

            human_score_column = get_human_score_column(prompt_name)
            
            row_data_list = [
                (idx, row, client, prompt_data, human_score_column, VLLM_MODEL_NAME, MAX_NEW_TOKENS)
                for idx, row in df.iterrows()
            ]
            
            workers = args.max_workers if args.max_workers else MAX_WORKERS
            results = []
            error_count = 0

            print(f"   🚀 Processing {len(row_data_list)} rows with {workers} concurrent workers...")
            
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_row = {executor.submit(process_single_row, rd): rd[1] for rd in row_data_list}
                for future in tqdm(as_completed(future_to_row), total=len(future_to_row), ncols=80, desc="Processing", leave=False):
                    try:
                        res = future.result()
                        if res["status"] == "success":
                            results.append({
                                "trial_num": res["trial_num"], "question_num": res.get("question_num", None),
                                "question": res["question"], "chatbot_answer": res["chatbot_answer"],
                                "human_score": res["human_score"],
                                "retrieved_context": res.get("retrieved_context", "NO_RAG_CONTROL"),
                                "ai_reasoning": res["ai_reasoning"], "ai_score": res["ai_score"],
                                "prompt_file": prompt_name, "prompt_variation": variation_name, "model": model_short
                            })
                        else:
                            # Save error cases too
                            results.append({
                                "status": res["status"], "reason": res.get("reason", "unknown"),
                                "trial_num": res.get("trial_num", "unknown"), "question_num": res.get("question_num", None),
                                "question": res.get("question", ""), "chatbot_answer": res.get("chatbot_answer", ""),
                                "human_score": res.get("human_score", None),
                                "retrieved_context": res.get("retrieved_context", "NO_RAG_CONTROL"),
                                "ai_reasoning": res.get("ai_reasoning", ""), "ai_score": res.get("ai_score", -1),
                                "prompt_file": prompt_name, "prompt_variation": variation_name, "model": model_short
                            })
                            error_count += 1
                            if error_count <= 5:  # Print first 5 errors
                                print(f"\n⚠️ Error processing {res.get('trial_num', 'unknown')}: {res.get('reason', 'unknown')}")
                    except Exception as e:
                        error_count += 1
                        if error_count <= 5:
                            print(f"\n⚠️ Unexpected error: {e}")
            
            if error_count > 5:
                print(f"\n⚠️ Total errors: {error_count} (showing first 5)")

            if save_atomic(results, output_path):
                print(f"      ✅ Saved {output_filename}")

    print("\n✅ ALL VARIATIONS COMPLETE.")

if __name__ == "__main__":
    main()