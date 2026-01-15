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
# Prompt variations configuration (Matches MedGemma logic)
PROMPT_VARIATIONS = {
    "def_demo": {
        "base_dir": "prompt_comparison_folder/prompts_def_only/",
        "output": "LAJ_score_QWEN_def_demo/"
    },
    "def_cot_demo": {
        "base_dir": "prompt_comparison_folder/prompts_cot_v4/",
        "output": "LAJ_score_QWEN_def_cot_demo/"
    }
}

# Directory containing demonstration files
DEMONSTRATIONS_DIR = "prompt_comparison_folder/demonstrations/"

EXCLUDED_PROMPTS = [
    "1_critical_harm.json",
    "2_individualized_advice.json",
    "3_persuation.json",
    "6_useful.json",
    "7_clarity.json",
    "5_acc_prompt.json"] 
INPUT_CSV = "data_preparation/QA_v9_verified_1224.csv"  # Changed from 1209 to 1224

# vLLM Configuration for Qwen3-30B
USE_VLLM = True 
VLLM_API_BASE = os.getenv("VLLM_API_BASE", "http://localhost:8000/v1")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "qwen3-30b")  # Changed to match served model name

TEST_MODE = False 
MAX_NEW_TOKENS = 1024
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "12")) 

FILENAME_TO_COLUMN = {
    "1_critical_harm": "Presence of Critical Harms Risks",
    "2_individualized_advice": "Presence of unqualified individualized medical advice",
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

def load_demonstrations(prompt_name):
    """Load demonstrations for a specific prompt."""
    demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{prompt_name}_demos.json")
    try:
        with open(demo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("demonstrations", [])
    except FileNotFoundError:
        return [] 
    except Exception as e:
        print(f"⚠️ Warning: Could not load demonstrations from {demo_file}: {e}")
        return []

def format_demonstrations(demos):
    """Format demonstrations into a string to insert into the prompt."""
    if not demos:
        return ""
    
    demo_text = "\n\n### EXAMPLES\n"
    for i, demo in enumerate(demos, 1):
        demo_text += f"\n**Example {i}:**\n"
        demo_text += f"Question: {demo.get('question', 'N/A')}\n"
        demo_text += f"Chatbot Answer: {demo.get('answer', 'N/A')}\n"
        demo_text += f"Evaluation: {demo.get('ai_reasoning', 'N/A')}\n"
        demo_text += f"Score: {demo.get('final_score', 'N/A')}\n"
    
    return demo_text

def extract_score(text):
    try:
        json_match = re.search(r'\{[^{}]*"final_score"[^{}]*\}', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            parsed = json.loads(json_str)
            if "final_score" in parsed:
                return int(parsed["final_score"])
    except:
        pass
    
    # Fallback regex extraction
    m = re.search(r'"final_score"\s*[:=]\s*(\d+)', text)
    if m: return int(m.group(1))
    
    m = re.search(r'\bfinal_score\b[^0-9]{0,10}(\d+)', text, flags=re.I)
    if m: return int(m.group(1))
    
    return 0

def format_messages(prompt_data, context, q, a, demos=None):
    """
    Format messages for Qwen.
    """
    system_role = "You are a clinical auditor."
    user_text = f"Context:\n{context}\n\nQuestion: {q}\nAnswer: {a}\n\nPlease evaluate."

    if prompt_data:
        system_role = prompt_data.get("system_role", system_role)
        if system_role == "": system_role = "You are a clinical auditor."
        
        if "instruction_template" in prompt_data:
            try:
                user_text = prompt_data["instruction_template"].format(
                    context=context, question=q, answer=a
                )
                
                # INJECT DEMONSTRATIONS
                if demos:
                    demo_text = format_demonstrations(demos)
                    if "### RESPONSE FORMAT" in user_text:
                        user_text = user_text.replace("### RESPONSE FORMAT", demo_text + "\n### RESPONSE FORMAT")
                    elif "### OUTPUT FORMAT" in user_text:
                        user_text = user_text.replace("### OUTPUT FORMAT", demo_text + "\n### OUTPUT FORMAT")
                    else:
                        user_text += demo_text

            except KeyError:
                 # Fallback for templates without context
                user_text = prompt_data["instruction_template"].format(question=q, answer=a)

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
    row, client, prompt_data, demos, human_score_column, model_name, max_tokens = row_data
    
    trial = str(row["trial_num"])
    question_num = row.get("question_num", None)  # Preserve question_num from CSV
    q, a = str(row["question"]), str(row["answer"])
    
    human_score = row.get(human_score_column, None) if human_score_column else None

    # DUMMY CONTEXT (NO RAG)
    ctx = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

    # Format with demos
    messages = format_messages(prompt_data, ctx, q, a, demos=demos)
    
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
        )
        resp_text = resp.choices[0].message.content
        ai_score = extract_score(resp_text)
    except Exception as e:
        return {
            "status": "error", "reason": str(e), "trial_num": trial, "question_num": question_num,
            "question": q, "chatbot_answer": a, "human_score": human_score,
            "ai_reasoning": "", "ai_score": 0
        }
    
    return {
        "status": "success", "trial_num": trial, "question_num": question_num, "question": q,
        "chatbot_answer": a, "human_score": human_score,
        "retrieved_context": "NO_RAG_CONTROL", "ai_reasoning": resp_text,
        "ai_score": ai_score
    }

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Qwen 30B NO-RAG evaluation with vLLM")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    parser.add_argument("--max_workers", type=int, default=None)
    args = parser.parse_args()

    # 1. Load Data
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    if TEST_MODE: df = df.head(5)

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
        BASE_PROMPTS_DIR = variation_config["base_dir"]
        OUTPUT_DIR = variation_config["output"]

        print(f"\n" + "="*80)
        print(f"📝 PROCESSING VARIATION: {variation_name.upper()}")
        print(f"   Base Prompts: {BASE_PROMPTS_DIR}")
        print(f"   Demos: {DEMONSTRATIONS_DIR}")
        print("="*80)

        if not os.path.exists(BASE_PROMPTS_DIR):
            print(f"⚠️ Base prompt directory missing: {BASE_PROMPTS_DIR}")
            continue
        
        if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

        prompt_files = discover_prompts(BASE_PROMPTS_DIR, EXCLUDED_PROMPTS)
        
        # 4. INNER LOOP: PROMPTS
        for prompt_file in prompt_files:
            prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
            output_filename = f"QA_{prompt_name}_{model_short}_{variation_name}_NO_RAG_1224.json"
            output_path = os.path.join(OUTPUT_DIR, output_filename)

            if os.path.exists(output_path) and not args.force:
                print(f"⏩ Skipping {output_filename} (Exists)")
                continue

            print(f"   📝 Running Prompt: {prompt_name}...")
            prompt_data = load_prompt(prompt_file)
            if not prompt_data: continue

            # Load Demonstrations
            demos = load_demonstrations(prompt_name)
            if demos: print(f"      📚 Injected {len(demos)} demonstrations.")
            
            human_score_column = get_human_score_column(prompt_name)

            row_data_list = [
                (row, client, prompt_data, demos, human_score_column, VLLM_MODEL_NAME, MAX_NEW_TOKENS)
                for _, row in df.iterrows()
            ]

            workers = args.max_workers if args.max_workers else MAX_WORKERS
            results = []

            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_row = {executor.submit(process_single_row, rd): rd[0] for rd in row_data_list}
                for future in tqdm(as_completed(future_to_row), total=len(future_to_row), leave=False):
                    res = future.result()
                    if res["status"] == "success":
                        results.append({
                            "question_num": res.get("question_num", None),
                            "trial_num": res["trial_num"], "question": res["question"],
                            "chatbot_answer": res["chatbot_answer"], "human_score": res["human_score"],
                            "retrieved_context": res.get("retrieved_context", "NO_RAG_CONTROL"),
                            "ai_reasoning": res["ai_reasoning"], "ai_score": res["ai_score"],
                            "prompt_file": prompt_name, "prompt_variation": variation_name, "model": model_short
                        })
                    else:
                        # Include error rows too, so output matches CSV row count
                        results.append({
                            "question_num": res.get("question_num", None),
                            "trial_num": res.get("trial_num", res.get("trial", "UNKNOWN")),
                            "question": res.get("question", ""),
                            "chatbot_answer": res.get("chatbot_answer", ""),
                            "human_score": res.get("human_score", None),
                            "retrieved_context": "NO_RAG_CONTROL",
                            "ai_reasoning": res.get("ai_reasoning", ""),
                            "ai_score": res.get("ai_score", 0),
                            "prompt_file": prompt_name,
                            "prompt_variation": variation_name,
                            "model": model_short,
                            "error": res.get("reason", "Unknown error")
                        })
                        print(f"⚠️ Error on trial {res.get('trial_num', res.get('trial', 'UNKNOWN'))}: {res.get('reason', 'Unknown error')}")

            if save_atomic(results, output_path):
                print(f"      ✅ Saved {output_filename}")

    print("\n✅ ALL VARIATIONS COMPLETE.")

if __name__ == "__main__":
    main()