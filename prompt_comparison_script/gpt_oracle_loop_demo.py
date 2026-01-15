import os
import glob
import json
import time
import pandas as pd
import re
import argparse
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx
from openai import OpenAI

# ======================================================
# CONFIGURATION
# ======================================================
KEY_FILE = "prompt_comparison_script/gpt_key.txt"
MODEL_NAME = "gpt-5.2"  # Changed from "gpt-5.1" to match noRAG version

# Prompt variations configuration
PROMPT_VARIATIONS = {
    "baseline": {
        "dir": "prompt_comparison_accuracy/baseline/",
        "output": "LAJ_score_GPT5_oracle_baseline/"
    },
    "def_only": {
        "dir": "prompt_comparison_accuracy/prompts_def_only/",
        "output": "LAJ_score_GPT5_oracle_def_only/"
    },
    "def_cot": {
        "dir": "prompt_comparison_accuracy/prompts_cot/",
        "output": "LAJ_score_GPT5_oracle_def_cot/"
    }
}

EXCLUDED_PROMPTS = []
INPUT_CSV = "data_preparation/raters.csv"
TEXT_FOLDER = "ICF_text_cleaned"  # Folder containing ICF text files
DEMONSTRATIONS_DIR = "prompt_comparison_accuracy/demonstrations/"  # ADD THIS
TEST_MODE = False

# ======================================================
# HELPER FUNCTIONS
# ======================================================

def load_api_key(filepath):
    try:
        with open(filepath, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        raise ValueError(f"API Key file not found at: {filepath}")

def discover_prompts(prompts_dir, excluded_list):
    """Find all JSON prompt files in directory."""
    if not os.path.exists(prompts_dir):
        return []
    files = glob.glob(os.path.join(prompts_dir, "*.json"))
    filtered = [f for f in files if os.path.basename(f) not in excluded_list]
    filtered.sort()
    return filtered

def load_prompt(prompt_file):
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading prompt {prompt_file}: {e}")
        return None

def safe(text):
    """Safe escape for prompt insertion."""
    if text is None:
        return ""
    return json.dumps(str(text))[1:-1]

def load_demonstrations(prompt_name):
    """
    Load demonstrations for a specific prompt from the demonstrations directory.
    Tries multiple naming patterns:
    1. {prompt_name}_demos.json (e.g., 5_factual_acc_short_v1_demos.json)
    2. Base name without version suffix (e.g., 5_factual_acc_demos.json)
    """
    # First try the full prompt name
    demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{prompt_name}_demos.json")
    if os.path.exists(demo_file):
        try:
            with open(demo_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("demonstrations", [])
        except Exception as e:
            print(f"⚠️ Warning: Could not load demonstrations from {demo_file}: {e}")
    
    # If not found, try base name (remove common suffixes like _short_v1, _v1, etc.)
    # Extract base name by removing version suffixes
    base_name = prompt_name
    # Remove patterns like _short_v1, _v1, _v2, etc.
    import re
    base_name = re.sub(r'_short_v\d+$', '', base_name)  # Remove _short_v1, _short_v2, etc.
    base_name = re.sub(r'_v\d+$', '', base_name)  # Remove _v1, _v2, etc.
    
    if base_name != prompt_name:
        demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{base_name}_demos.json")
        try:
            with open(demo_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("demonstrations", [])
        except FileNotFoundError:
            pass  # File doesn't exist, that's okay
        except Exception as e:
            print(f"⚠️ Warning: Could not load demonstrations from {demo_file}: {e}")
    
    return []  # No demonstrations file found

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

def get_full_text(trial_id):
    """
    Oracle method: Load the complete ICF text file for the trial.
    Files are already human-selected, so we only need to match by NCT number.
    """
    trial_id = str(trial_id).strip()
    for root, dirs, files in os.walk(TEXT_FOLDER):
        for file in files:
            # Match file that starts with trial_id and ends with .txt
            if file.startswith(trial_id) and file.endswith(".txt"):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        content = f.read().strip("\ufeff")
                        return content
                except Exception as e:
                    print(f"⚠ Error reading {file}: {e}")
                    continue
    return None

def build_prompt(prompt_data, context, question, answer):
    """Build prompt from template."""
    if prompt_data is None:
        raise ValueError("Prompt data is None. Cannot build prompt.")
    
    system_role = prompt_data.get("system_role", "You are a clinical auditor.")
    template = prompt_data.get("instruction_template", "")

    # Escape curly braces in the template (except for {context}, {question}, {answer})
    template = template.replace("{context}", "___CONTEXT_PLACEHOLDER___")
    template = template.replace("{question}", "___QUESTION_PLACEHOLDER___")
    template = template.replace("{answer}", "___ANSWER_PLACEHOLDER___")
    # Escape all remaining curly braces
    template = template.replace("{", "{{").replace("}", "}}")
    # Restore our placeholders
    template = template.replace("___CONTEXT_PLACEHOLDER___", "{context}")
    template = template.replace("___QUESTION_PLACEHOLDER___", "{question}")
    template = template.replace("___ANSWER_PLACEHOLDER___", "{answer}")

    # Format template with actual values
    try:
        user_text = template.format(
            context=context,
            question=safe(question),
            answer=safe(answer)
        )
    except KeyError:
        # Fallback if template doesn't have all placeholders
        user_text = template.format(
            question=safe(question),
            answer=safe(answer)
        )

    return system_role, user_text

def save_atomic(data, filepath):
    """Atomically save JSON data to file."""
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

def get_gpt_response(client, system_role, user_prompt):
    """
    Calls the GPT-5 API with reasoning and verbosity settings.
    """
    messages = []
    
    # Add System Role
    if system_role and system_role.strip():
        messages.append({
            "role": "system",
            "content": [{"type": "input_text", "text": system_role}]
        })

    # Add User Message
    messages.append({
        "role": "user",
        "content": [{"type": "input_text", "text": user_prompt}]
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
        
        output_content = response.output_text
        parsed_json = json.loads(output_content)
        
        return {
            "ai_reasoning": parsed_json.get("reasoning", ""),
            "ai_score": int(parsed_json.get("final_score", 0)),
            "raw_response": output_content
        }

    except Exception as e:
        return {"error": str(e)}

# ======================================================
# WORKER FUNCTION
# ======================================================
def process_row(row_data):
    """Process a single row with oracle (full text) method."""
    row, client, prompt_data, human_col, demo_text = row_data  # ADD demo_text parameter
    
    trial_id = str(row["trial_num"]).strip()
    question_num = row.get("question_num", None)
    question = str(row["question"]).strip()
    answer = str(row["answer"]).strip()
    human_score = row.get(human_col) if human_col and human_col in row else None

    # Oracle method: Load full ICF text
    full_text = get_full_text(trial_id)
    
    if not full_text:
        return {
            "status": "error",
            "trial_num": trial_id,
            "question_num": question_num,
            "error_msg": "Missing ICF text",
            "question": question,
            "chatbot_answer": answer,
            "human_score": human_score,
            "retrieved_context": "MISSING_FULL_TEXT",
            "retrieved_chunks_info": [],
            "ai_reasoning": "Missing ICF text",
            "ai_score": -1
        }

    context_block = full_text  # Use full text as context

    # Build prompt
    try:
        system_role, user_prompt = build_prompt(
            prompt_data, context_block, question, answer
        )
        
        # Inject demonstrations into user_prompt (ADD THIS SECTION)
        if demo_text:
            # Look for formatting sections to place demos before them
            if "### RESPONSE FORMAT" in user_prompt:
                user_prompt = user_prompt.replace("### RESPONSE FORMAT", demo_text + "\n### RESPONSE FORMAT")
            elif "### OUTPUT FORMAT" in user_prompt:
                user_prompt = user_prompt.replace("### OUTPUT FORMAT", demo_text + "\n### OUTPUT FORMAT")
            elif "### OUTPUT (STRICT JSON ONLY)" in user_prompt:
                user_prompt = user_prompt.replace("### OUTPUT (STRICT JSON ONLY)", demo_text + "\n### OUTPUT (STRICT JSON ONLY)")
            else:
                # If no known section, append to end
                user_prompt += demo_text
                
    except Exception as e:
        return {
            "status": "error",
            "trial_num": trial_id,
            "question_num": question_num,
            "error_msg": f"Prompt building error: {e}",
            "question": question,
            "chatbot_answer": answer,
            "human_score": human_score,
            "retrieved_context": "FULL_TEXT_ORACLE",
            "retrieved_chunks_info": [],
            "ai_reasoning": f"Prompt building error: {e}",
            "ai_score": -1
        }

    # Send to GPT
    try:
        time.sleep(0.4)  # Rate limiting

        result = get_gpt_response(client, system_role, user_prompt)
        
        if "error" in result:
            return {
                "status": "error",
                "trial_num": trial_id,
                "question_num": question_num,
                "error_msg": result["error"],
                "question": question,
                "chatbot_answer": answer,
                "human_score": human_score,
                "retrieved_context": "FULL_TEXT_ORACLE",
                "retrieved_chunks_info": [],
                "ai_reasoning": f"API ERROR: {result['error']}",
                "ai_score": -1
            }

        ai_score = result["ai_score"]
        reasoning = result["ai_reasoning"]

    except Exception as e:
        return {
            "status": "error",
            "trial_num": trial_id,
            "question_num": question_num,
            "error_msg": f"API ERROR: {e}",
            "question": question,
            "chatbot_answer": answer,
            "human_score": human_score,
            "retrieved_context": "FULL_TEXT_ORACLE",
            "retrieved_chunks_info": [],
            "ai_reasoning": f"API ERROR: {e}",
            "ai_score": -1
        }

    return {
        "status": "success",
        "trial_num": trial_id,
        "question_num": question_num,
        "question": question,
        "chatbot_answer": answer,
        "human_score": human_score,
        "scales_used": "FULL_TEXT_ORACLE",
        "retrieved_context": "FULL_TEXT_ORACLE",
        "retrieved_chunks_info": [],
        "ai_reasoning": reasoning,
        "ai_score": ai_score
    }

# ======================================================
# MAIN
# ======================================================
def main():
    parser = argparse.ArgumentParser(description="GPT-5 Oracle Judge with Prompt Variations")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent API requests")
    args = parser.parse_args()

    print(f"--- STARTING GPT-5 ORACLE JUDGE ({MODEL_NAME}) ---")

    # Load API Key
    print(f"🔑 Loading API Key from {KEY_FILE}...")
    api_key = load_api_key(KEY_FILE)
    
    # Create client with SSL bypass (if needed)
    http_client = httpx.Client(verify=False)
    client = OpenAI(api_key=api_key, http_client=http_client)

    # Load CSV
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    df["answer"] = df["answer"].astype(str)
    df = df[df["answer"].str.strip() != ""]
    df = df[df["answer"].str.lower() != "nan"]

    if TEST_MODE:
        print("🔬 TEST MODE ON: Processing only first 5 rows.")
        df = df.head(5)

    print(f"Loaded {len(df)} rows.\n")

    # Human score column for accuracy
    HUMAN_SCORE_COLUMN = "Level of factual accuracy"

    # Outer Loop: Prompt Variations
    for variation_name, variation_config in PROMPT_VARIATIONS.items():
        prompts_dir = variation_config["dir"]
        output_dir = variation_config["output"]

        print(f"\n" + "="*80)
        print(f"📝 PROCESSING VARIATION: {variation_name.upper()}")
        print(f"   Prompts: {prompts_dir}")
        print(f"   Output: {output_dir}")
        print(f"   Demos:   {DEMONSTRATIONS_DIR}")  # ADD THIS LINE
        print("="*80)

        if not os.path.exists(prompts_dir):
            print(f"⚠️ Skipping {variation_name}: Directory not found {prompts_dir}")
            continue

        prompt_files = discover_prompts(prompts_dir, EXCLUDED_PROMPTS)
        if not prompt_files:
            print(f"⚠️ No prompt files found in {prompts_dir}")
            continue

        # Inner Loop: Prompts
        for prompt_file in prompt_files:
            prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
            output_filename = f"{prompt_name}_{variation_name}_RAG_demo_raters.json"
            output_path = os.path.join(output_dir, output_filename)

            if os.path.exists(output_path) and not args.force:
                print(f"⏩ Skipping {prompt_name} (Exists)")
                continue

            print(f"   📝 Running Prompt: {prompt_name}...")
            prompt_data = load_prompt(prompt_file)
            if not prompt_data:
                continue

            # Load Demonstrations for this specific prompt (ADD THIS SECTION)
            demos = load_demonstrations(prompt_name)
            demo_text = format_demonstrations(demos)
            
            if demos:
                print(f"      📚 Injected {len(demos)} demonstrations.")
            else:
                print(f"      ⚠️ No demonstrations found.")

            # Prepare row data (ADD demo_text to the tuple)
            row_data_list = [
                (row, client, prompt_data, HUMAN_SCORE_COLUMN, demo_text)
                for _, row in df.iterrows()
            ]

            results = []
            error_count = 0

            # Process concurrently
            print(f"   🚀 Processing {len(row_data_list)} rows with {args.workers} workers...")
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {executor.submit(process_row, rd): rd[0] for rd in row_data_list}
                
                for future in tqdm(as_completed(futures), total=len(futures), leave=False):
                    res = future.result()
                    if res["status"] == "success":
                        # Add prompt metadata
                        res["prompt_file"] = prompt_name
                        res["prompt_variation"] = variation_name
                        res["model"] = MODEL_NAME
                        del res["status"]
                        results.append(res)
                    else:
                        error_count += 1
                        if error_count <= 5:
                            print(f"\n⚠️ Error on trial {res.get('trial_num', 'unknown')}: {res.get('error_msg', 'unknown')}")

            if error_count > 5:
                print(f"\n⚠️ Total errors: {error_count} (showing first 5)")

            # Save results
            if save_atomic(results, output_path):
                print(f"      ✅ Saved {len(results)} rows to {output_filename}")
            else:
                print(f"      ❌ Failed to save {output_filename}")

    print("\n✅ ALL VARIATIONS COMPLETE.")

if __name__ == "__main__":
    main()

