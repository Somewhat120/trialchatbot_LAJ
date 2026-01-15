import os
import glob
import json
import pandas as pd
import argparse
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ==========================================
# CONFIGURATION
# ==========================================
# Prompt variations (Updated to match your demonstration folders)
PROMPT_VARIATIONS = {
    "def_demo": {
        "dir": "prompt_comparison_folder/prompts_def_only/",
        "output": "LAJ_score_GPT5_def_demo/"
    },
    "def_cot_demo": {
        "dir": "prompt_comparison_folder/prompts_cot_v4/",
        "output": "LAJ_score_GPT5_def_cot_demo/"
    }
}

DEMONSTRATIONS_DIR = "prompt_comparison_folder/demonstrations/"
EXCLUDED_PROMPTS = ["5_acc_prompt.json"] 
INPUT_CSV = "data_preparation/QA_v9_verified_1224.csv"  # Updated to match MedGemma
KEY_FILE = "prompt_comparison_script/gpt_key.txt"
MODEL_NAME = "gpt-5.2"

FILENAME_TO_COLUMN = {
    "1_critical_harm": "Presence of Critical Harms Risks",
    "2_individualized_advice": "Presence of unqualified individualized medical advice",
    "3_persuation": "Presence of inappropriately persuasion",
    "4_transparent": "Level of transparency",  # Changed from "4_transparency"
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

def load_demonstrations(prompt_name):
    """
    Load demonstrations for a specific prompt from the demonstrations directory.
    Matches filename pattern: {prompt_name}_demos.json
    """
    demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{prompt_name}_demos.json")
    try:
        with open(demo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("demonstrations", [])
    except FileNotFoundError:
        return []  # No demonstrations file is acceptable
    except Exception as e:
        print(f"⚠️ Warning: Could not load demonstrations from {demo_file}: {e}")
        return []

def format_demonstrations(demos):
    """
    Format demonstrations into a string to insert into the prompt.
    """
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
    Calls the GPT-5 API.
    """
    messages = []
    
    # Add System Role
    if system_role and system_role.strip():
        messages.append({
            "role": "system",
            "content": [{"type": "input_text", "text": system_role}]  # Changed from "text" to "input_text"
        })

    # Add User Message (Which now includes the Template + Demos + Q/A)
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
                "verbosity": "low"
            },
            reasoning={"effort": "none"},
            store=False
        )
        
        output_content = response.output_text
        parsed_json = json.loads(output_content)
        
        return {
            "ai_reasoning": parsed_json.get("reasoning", ""),
            "ai_score": int(parsed_json.get("final_score", 0)),
            "raw_response": output_content
        }

    except Exception as e:
        return {"error": str(e)}

# ==========================================
# WORKER FUNCTION
# ==========================================
def process_row(row_data):
    row, client, prompt_data, demo_text, human_col = row_data
    
    q = str(row["question"])
    a = str(row["answer"])
    trial = str(row["trial_num"])
    question_num = row.get("question_num", None)  # Preserve question_num from CSV
    
    human_score = row.get(human_col) if human_col in row else None

    # 1. Get System Role and Template
    system_role = prompt_data.get("system_role", "")
    template = prompt_data.get("instruction_template", "")
    
    # 2. Inject Data (Q & A) into Template
    try:
        user_text = template.format(question=q, answer=a)
    except KeyError:
        # Fallback
        user_text = f"Question: {q}\nAnswer: {a}\n\n{template}"

    # 3. Inject Demonstrations into User Text
    # We look for the formatting section to place demos immediately before it
    if demo_text:
        if "### RESPONSE FORMAT" in user_text:
            user_text = user_text.replace("### RESPONSE FORMAT", demo_text + "\n### RESPONSE FORMAT")
        elif "### OUTPUT FORMAT" in user_text:
            user_text = user_text.replace("### OUTPUT FORMAT", demo_text + "\n### OUTPUT FORMAT")
        else:
            # If no known section, append to end
            user_text += demo_text

    # 4. Call API
    result = get_gpt_response(client, system_role, user_text)
    
    if "error" in result:
        return {
            "status": "error",
            "trial_num": trial,
            "question_num": question_num,
            "question": q,
            "chatbot_answer": a,
            "human_score": human_score,
            "error_msg": result["error"]
        }
    
    return {
        "status": "success",
        "trial_num": trial,
        "question_num": question_num,
        "question": q,
        "chatbot_answer": a,
        "human_score": human_score,
        "ai_reasoning": result["ai_reasoning"],
        "ai_score": result["ai_score"]
    }

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent API requests")
    args = parser.parse_args()

    # 1. Setup Client
    print(f"🔑 Loading API Key from {KEY_FILE}...")
    api_key = load_api_key(KEY_FILE)
    client = OpenAI(api_key=api_key)

    # 2. Load Data
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)

    # 3. Outer Loop: Prompt Variations
    for var_name, var_config in PROMPT_VARIATIONS.items():
        prompts_dir = var_config["dir"]
        output_dir = var_config["output"]

        print(f"\n" + "="*80)
        print(f"📝 PROCESSING VARIATION: {var_name.upper()}")
        print(f"   Prompts: {prompts_dir}")
        print(f"   Demos:   {DEMONSTRATIONS_DIR}")
        print("="*80)

        if not os.path.exists(prompts_dir):
            print(f"⚠️ Skipping {var_name}: Dir not found {prompts_dir}")
            continue

        prompt_files = discover_prompts(prompts_dir, EXCLUDED_PROMPTS)
        
        # 4. Inner Loop: Prompts
        for prompt_file in prompt_files:
            prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
            output_filename = f"QA_{prompt_name}_gpt5_{var_name}_1224.json"
            output_path = os.path.join(output_dir, output_filename)

            if os.path.exists(output_path) and not args.force:
                print(f"⏩ Skipping {prompt_name} (Exists)")
                continue

            print(f"   📝 Running Prompt: {prompt_name}")
            prompt_data = load_prompt(prompt_file)
            if not prompt_data: continue

            # Load Demonstrations for this specific prompt
            demos = load_demonstrations(prompt_name)
            demo_text = format_demonstrations(demos)
            
            if demos:
                print(f"      📚 Injected {len(demos)} demonstrations.")
            else:
                print(f"      ⚠️ No demonstrations found.")

            human_col = FILENAME_TO_COLUMN.get(prompt_name)

            # Prepare jobs
            # Note: We pass 'demo_text' to the worker now
            row_data_list = [(row, client, prompt_data, demo_text, human_col) for _, row in df.iterrows()]
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
                        # Include error rows too, so output matches CSV row count
                        results.append({
                            "question_num": res.get("question_num", None),
                            "trial_num": res.get("trial_num", "UNKNOWN"),
                            "question": res.get("question", ""),
                            "chatbot_answer": res.get("chatbot_answer", ""),
                            "human_score": res.get("human_score", None),
                            "ai_reasoning": "",
                            "ai_score": 0,
                            "error": res.get("error_msg", "Unknown error")
                        })
                        print(f"⚠️ Error on trial {res.get('trial_num', 'UNKNOWN')}: {res.get('error_msg', 'Unknown error')}")

            if save_atomic(results, output_path):
                print(f"      ✅ Saved {len(results)} rows to {output_filename}")

if __name__ == "__main__":
    main()