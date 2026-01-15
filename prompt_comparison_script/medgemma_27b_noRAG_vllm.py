import os
import glob
import json
import pandas as pd
import torch
import re
import gc
import tempfile
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForCausalLM

# ==========================================
# CONFIGURATION
# ==========================================
PROMPTS_DIR = "prompts_v6/"
EXCLUDED_PROMPTS = ["5_acc_prompt.json"] 
INPUT_CSV = "data_preparation/QA_v9_verified_1224.csv"
OUTPUT_DIR = "LAJ_score_NO_RAG_baseline/" # Updated to match v3 prompts

MODELS_LIST = [
    "models/medgemma-27b-text-it"
]

TEST_MODE = False  # Set to True for a quick debug run
MAX_NEW_TOKENS = 1024
REPETITION_PENALTY = 1.0

# Mapping from prompt filename (without extension) to CSV column name for human scores
FILENAME_TO_COLUMN = {
    "1_critical_harm": "Presence of Critical Harms Risks",
    "2_individualized_advice": "Presence of unqualified individualized medical advice",
    "3_persuation": "Presence of inappropriately persuasion",
    "4_transparency": "Level of transparency",
    "6_useful": "Level of usefulness",
    "7_clarity": "Level of Clarity and Succinctness"
}

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def discover_prompts(prompts_dir, excluded_list):
    """Finds all JSON prompts except the excluded ones."""
    files = glob.glob(os.path.join(prompts_dir, "*.json"))
    filtered = [f for f in files if os.path.basename(f) not in excluded_list]
    filtered.sort()
    return filtered

def get_model_short_name(model_path):
    """Converts 'models/gemma-3-4b-it' -> 'gemma_3_4b_it'"""
    name = os.path.basename(model_path)
    return name.replace("-", "_").replace(".", "")

def get_human_score_column(prompt_name):
    """
    Get the CSV column name for human scores based on prompt filename.
    Returns None if mapping not found.
    """
    return FILENAME_TO_COLUMN.get(prompt_name, None)

def load_prompt(prompt_file):
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading prompt {prompt_file}: {e}")
        return None

def extract_score(text):
    """
    Extract score from model response.
    Prompts_v3 expect JSON with "final_score" key, not "score".
    """
    # First try to parse as JSON (more robust)
    try:
        # Try to find JSON object in the text
        json_match = re.search(r'\{[^{}]*"final_score"[^{}]*\}', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            # Try to extract complete JSON (handle nested braces)
            brace_count = 0
            start_idx = json_match.start()
            for i in range(start_idx, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_str = text[start_idx:i+1]
                        parsed = json.loads(json_str)
                        if "final_score" in parsed:
                            return int(parsed["final_score"])
                        break
    except:
        pass
    
    # Fallback: Look for "final_score": 5 (with quotes)
    m = re.search(r'"final_score"\s*[:=]\s*(\d+)', text)
    if m: 
        return int(m.group(1))
    
    # Also check for "score" as fallback (for backward compatibility)
    m = re.search(r'"score"\s*[:=]\s*(\d+)', text)
    if m: 
        return int(m.group(1))
    
    # Look for final_score: 5 (without quotes)
    m = re.search(r'\bfinal_score\b[^0-9]{0,10}(\d+)', text, flags=re.I)
    if m: 
        return int(m.group(1))
    
    # Look for score: 5 (without quotes) - backward compatibility
    m = re.search(r'\bscore\b[^0-9]{0,10}(\d+)', text, flags=re.I)
    if m: 
        return int(m.group(1))
    
    return 0

def format_messages(prompt_data, context, q, a):
    # Default fallback
    system_role = "You are a clinical auditor."
    user_text = f"Context:\n{context}\n\nQuestion: {q}\nAnswer: {a}\n\nRate factual accuracy on a 1-5 scale."

    if prompt_data and "instruction_template" in prompt_data:
        system_role = prompt_data.get("system_role", system_role)
        try:
            # Most prompts_v3 don't use {context}, but we provide it for compatibility
            user_text = prompt_data["instruction_template"].format(
                context=context, question=q, answer=a
            )
        except KeyError as e:
            print(f"⚠️ Prompt template key error: {e}")
            
    return [{"role": "system", "content": system_role}, {"role": "user", "content": user_text}]

def save_atomic(data, filepath):
    """
    Atomically save JSON data to file.
    Writes to temp file first, then renames to final destination.
    """
    # Create temp file in same directory as final file
    dirname = os.path.dirname(filepath)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    
    # Create temp file with .tmp extension
    temp_path = filepath + ".tmp"
    
    try:
        # Write to temp file
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        
        # Atomic rename (works on Unix/Linux)
        os.rename(temp_path, filepath)
        return True
    except Exception as e:
        # Clean up temp file on error
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        print(f"❌ Error saving {filepath}: {e}")
        return False

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    # 0. Setup
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    prompt_files = discover_prompts(PROMPTS_DIR, EXCLUDED_PROMPTS)
    print(f"📋 Found {len(prompt_files)} prompts to test.")
    
    # 1. Load Data
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    if TEST_MODE:
        print("🔬 TEST MODE ON: Processing only first 5 rows.")
        df = df.head(5)

    # 2. OUTER LOOP: MODELS (For Efficiency)
    for model_path in MODELS_LIST:
        print(f"\n" + "="*60)
        print(f"🤖 LOADING MODEL: {model_path}")
        print("="*60)

        # Load Model & Tokenizer
        try:
            tokenizer = AutoTokenizer.from_pretrained(model_path)
            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                dtype=torch.bfloat16,  # Fixed: was torch_dtype (deprecated)
                device_map="auto"
            )
        except Exception as e:
            print(f"❌ Failed to load {model_path}: {e}")
            continue # Skip to next model

        model_short = get_model_short_name(model_path)

        # 3. INNER LOOP: PROMPTS
        for prompt_file in prompt_files:
            prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
            output_filename = f"QA_{prompt_name}_{model_short}_NO_RAG.json"
            output_path = os.path.join(OUTPUT_DIR, output_filename)

            if os.path.exists(output_path):
                print(f"⏩ Skipping {output_filename} (Already exists)")
                continue

            print(f"   📝 Running Prompt: {prompt_name}...")
            prompt_data = load_prompt(prompt_file)
            
            if prompt_data is None:
                print(f"   ⚠️ Skipping {prompt_name} (Failed to load prompt)")
                continue
            
            # Get the correct CSV column name for human scores based on prompt name
            human_score_column = get_human_score_column(prompt_name)
            if human_score_column is None:
                print(f"   ⚠️ Warning: No mapping found for prompt '{prompt_name}'. Human scores will be None.")
            else:
                # Verify column exists in CSV
                if human_score_column not in df.columns:
                    print(f"   ⚠️ Warning: Column '{human_score_column}' not found in CSV. Human scores will be None.")
                    human_score_column = None
            
            results = []
            
            # Process Rows
            for idx, row in tqdm(df.iterrows(), total=len(df), leave=False):
                trial = str(row["trial_num"])
                q, a = str(row["question"]), str(row["answer"])
                
                # Get human score using the mapped column name
                if human_score_column:
                    human_score = row.get(human_score_column, None)
                else:
                    human_score = None

                # DUMMY CONTEXT (NO RAG)
                ctx = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

                messages = format_messages(prompt_data, ctx, q, a)
                
                # Inference
                inputs = tokenizer.apply_chat_template(
                    messages, 
                    add_generation_prompt=True, 
                    tokenize=True, 
                    return_dict=True, 
                    return_tensors="pt"
                )
                inputs = {k: v.to(model.device) for k, v in inputs.items()}
                input_len = inputs["input_ids"].shape[-1]

                with torch.no_grad():
                    output = model.generate(
                        **inputs, 
                        max_new_tokens=MAX_NEW_TOKENS, 
                        do_sample=False,
                        repetition_penalty=REPETITION_PENALTY
                    )
                
                resp = tokenizer.decode(output[0][input_len:], skip_special_tokens=True)
                
                results.append({
                    "trial_num": trial,
                    "question": q,
                    "chatbot_answer": a,
                    "human_score": human_score,  # Now correctly mapped per prompt
                    "retrieved_context": "NO_RAG_CONTROL",
                    "ai_reasoning": resp,
                    "ai_score": extract_score(resp),  # AI score extracted from response
                    "prompt_file": prompt_name,
                    "model": model_short
                })

            # Save results atomically for this prompt/model combo
            if save_atomic(results, output_path):
                print(f"      ✅ Saved {output_filename}")
            else:
                print(f"      ❌ Failed to save {output_filename}")

        # 4. Cleanup after Model is done with ALL prompts
        print(f"🧹 Unloading {model_short}...")
        del model
        del tokenizer
        torch.cuda.empty_cache()
        gc.collect()

    print("\n✅ GRID SEARCH COMPLETE.")

if __name__ == "__main__":
    main()