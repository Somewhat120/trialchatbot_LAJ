import os
import glob
import json
import pandas as pd
import argparse
import re
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx
from openai import OpenAI

# ==========================================
# CONFIGURATION
# ==========================================
TEST_MODE = False # Set to True for debugging (3 rows only)

# Only these variations (No Demos)
PROMPT_VARIATIONS = {
    "baseline": {
        "dir": "prompt_comparison_folder/baseline/",
        "output": "LAJ_score_GPT5_baseline/"
    },
    "def_only": {
        "dir": "prompt_comparison_folder/prompts_def_only/",
        "output": "LAJ_score_GPT5_def_only/"
    },
    "def_cot": {
        "dir": "prompt_comparison_folder/prompts_cot/",
        "output": "LAJ_score_GPT5_def_cot/"
    }
}

EXCLUDED_PROMPTS = [
    "5_acc_prompt.json"]
INPUT_CSV = "data_preparation/JCO_FAQ.csv"
KEY_FILE = "prompt_comparison_script/gpt_key.txt"
MODEL_NAME = "gpt-5.2"

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

def load_api_key(filepath):
    try:
        with open(filepath, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        raise ValueError(f"API Key file not found at: {filepath}")

def load_prompt(prompt_file):
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading prompt {prompt_file}: {e}")
        return None

def discover_prompts(prompts_dir, excluded_list):
    files = glob.glob(os.path.join(prompts_dir, "*.json"))
    filtered = [f for f in files if os.path.basename(f) not in excluded_list]
    filtered.sort()
    return filtered

def save_atomic(data, filepath):
    dirname = os.path.dirname(filepath)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    temp_path = filepath + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.rename(temp_path, filepath)
        return True
    except Exception as e:
        print(f"❌ Error saving {filepath}: {e}")
        return False

def get_gpt_response(client, system_role, final_prompt_text):
    """
    Calls the GPT-5 API with corrected content types.
    Returns raw response in ai_reasoning if parsing fails.
    """
    messages = []
    
    # Add System Role - Updated type to 'input_text'
    if system_role and system_role.strip():
        messages.append({
            "role": "system",
            "content": [{"type": "input_text", "text": system_role}]
        })

    # Add User Message
    messages.append({
        "role": "user",
        "content": [{"type": "input_text", "text": final_prompt_text}]
    })

    try:
        response = client.responses.create(
            model=MODEL_NAME,
            input=messages,
            text={
                "format": {"type": "json_object"},
                "verbosity": "medium"
            },
            reasoning={"effort": "low"},
            store=False
        )
        
        # Access the output - Ensure the attribute name matches your SDK version
        output_content = response.output_text
        
        # Try to parse JSON, but preserve raw response if parsing fails
        try:
            parsed_json = json.loads(output_content)
            
            # Extract reasoning (with fallback to raw if missing)
            reasoning = parsed_json.get("reasoning", "")
            if not reasoning:
                reasoning = output_content  # Use raw response if no reasoning field
            
            # Extract score (with safe conversion)
            try:
                ai_score = int(parsed_json.get("final_score", 0))
            except (ValueError, TypeError):
                # If final_score is not a valid int, try to extract from text
                score_match = re.search(r'final_score[^0-9]{0,10}(\d)', output_content, flags=re.IGNORECASE)
                ai_score = int(score_match.group(1)) if score_match else -1
            
            return {
                "ai_reasoning": reasoning,
                "ai_score": ai_score,
                "raw_response": output_content
            }
        except json.JSONDecodeError as json_err:
            # JSON parsing failed - save raw response in ai_reasoning
            return {
                "ai_reasoning": output_content,  # Save raw response
                "ai_score": -1,  # Use -1 to indicate parsing failure
                "raw_response": output_content,
                "parse_error": str(json_err)
            }

    except Exception as e:
        return {
            "error": str(e),
            "ai_reasoning": "",  # No response available
            "ai_score": -1
        }
# ==========================================
# WORKER FUNCTION
# ==========================================
def process_row(row_data):
    # REMOVED demo_text argument
    row, client, prompt_data, human_col = row_data
    
    # Safe access with defaults
    q = str(row.get("question", ""))
    a = str(row.get("answer", ""))
    trial = str(row.get("trial_num", "UNKNOWN"))
    
    # Extract question_num (with fallback)
    question_num = row.get("question_num", None)
    if question_num is not None:
        try:
            question_num = int(float(question_num)) if pd.notna(question_num) else None
        except (ValueError, TypeError):
            question_num = None
    
    human_score = row.get(human_col) if human_col and human_col in row else None

    # 1. Get System Role and Template
    system_role = prompt_data.get("system_role", "")
    template = prompt_data.get("instruction_template", "")
    
    # 2. Inject Data (Q & A) into Template
    try:
        user_text = template.format(question=q, answer=a)
    except KeyError:
        # Fallback if keys don't match
        user_text = f"Question: {q}\nAnswer: {a}\n\n{template}"

    # 3. Call API
    result = get_gpt_response(client, system_role, user_text)
    
    if "error" in result:
        return {
            "status": "error",
            "trial_num": trial,
            "question_num": question_num,
            "question": q,
            "chatbot_answer": a,
            "human_score": human_score,
            "error_msg": result["error"],
            "ai_reasoning": result.get("ai_reasoning", ""),
            "ai_score": result.get("ai_score", -1)
        }
    
    return {
        "status": "success",
        "trial_num": trial,
        "question_num": question_num,
        "question": q,
        "chatbot_answer": a,
        "human_score": human_score,
        "ai_reasoning": result.get("ai_reasoning", ""),
        "ai_score": result.get("ai_score", -1)
    }

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent API requests")
    args = parser.parse_args()

# 1. Setup Client with SSL bypass
    print(f"🔑 Loading API Key from {KEY_FILE}...")
    api_key = load_api_key(KEY_FILE)
    
    # Create a custom HTTP client that ignores SSL errors
    http_client = httpx.Client(verify=False)
    
    client = OpenAI(
        api_key=api_key,
        http_client=http_client
    )
    # 2. Load Data
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)

    # --- TEST MODE LOGIC ---
    if TEST_MODE:
        print("\n" + "!"*50)
        print("🔬 TEST MODE ON: Processing only first 3 rows.")
        print("!"*50 + "\n")
        df = df.head(3)
    # -----------------------

    # 3. Outer Loop: Prompt Variations
    for var_name, var_config in PROMPT_VARIATIONS.items():
        prompts_dir = var_config["dir"]
        output_dir = var_config["output"]

        print(f"\n" + "="*80)
        print(f"📝 PROCESSING VARIATION: {var_name.upper()}")
        print(f"   Prompts: {prompts_dir}")
        print("="*80)

        if not os.path.exists(prompts_dir):
            print(f"⚠️ Skipping {var_name}: Dir not found {prompts_dir}")
            continue

        prompt_files = discover_prompts(prompts_dir, EXCLUDED_PROMPTS)
        
        # 4. Inner Loop: Prompts
        for prompt_file in prompt_files:
            prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
            output_filename = f"QA_{prompt_name}_gpt5_{var_name}_low_JCO_FAQ.json"
            output_path = os.path.join(output_dir, output_filename)

            if os.path.exists(output_path) and not args.force:
                print(f"⏩ Skipping {prompt_name} (Exists)")
                continue

            print(f"   📝 Running Prompt: {prompt_name}")
            prompt_data = load_prompt(prompt_file)
            if not prompt_data: continue

            # REMOVED: demos = load_demonstrations...

            human_col = FILENAME_TO_COLUMN.get(prompt_name)

            # Prepare jobs (REMOVED demo_text from list)
            row_data_list = [(row, client, prompt_data, human_col) for _, row in df.iterrows()]
            results = []

            # Run Concurrently
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {executor.submit(process_row, rd): rd for rd in row_data_list}
                
                for future in tqdm(as_completed(futures), total=len(futures), leave=False):
                    res = future.result()
                    if res["status"] == "success":
                        results.append(res)
                        del res["status"]
                    else:
                        print(f"⚠️ Error on trial {res['trial_num']}: {res['error_msg']}")

            if save_atomic(results, output_path):
                print(f"      ✅ Saved {len(results)} rows to {output_filename}")

if __name__ == "__main__":
    main()