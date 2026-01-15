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
from openai import OpenAI  # For vLLM support

# ==========================================
# CONFIGURATION
# ==========================================
# Prompt variations that use demonstrations
PROMPT_VARIATIONS = {
    "def_demo": {
        "base_dir": "prompt_comparison_folder/prompts_def_only/",  # Base prompts without demos
        "output": "LAJ_score_NO_RAG_def_demo/"
    },
    "def_cot_demo": {
        "base_dir": "prompt_comparison_folder/prompts_cot_v4/",  # Base prompts without demos
        "output": "LAJ_score_NO_RAG_def_cot_demo/"
    }
}

# Directory containing demonstration files
DEMONSTRATIONS_DIR = "prompt_comparison_folder/demonstrations/"

EXCLUDED_PROMPTS = ["5_acc_prompt.json"] 
INPUT_CSV = "data_preparation/QA_v9_verified_1224.csv"

MODELS_LIST = [
    "models/medgemma-27b-text-it"
]

# vLLM Configuration
USE_VLLM = True  # Set to False to use direct HuggingFace loading
VLLM_API_BASE = os.getenv("VLLM_API_BASE", "http://localhost:8000/v1")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "medgemma-27b")  # Must match --served-model-name

TEST_MODE = False  # Set to True for a quick debug run
MAX_NEW_TOKENS = 1024
REPETITION_PENALTY = 1.0

# Mapping from prompt filename (without extension) to CSV column name for human scores
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

def load_demonstrations(prompt_name):
    """
    Load demonstrations for a specific prompt from the demonstrations directory.
    Returns empty list if no demonstrations file found.
    """
    demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{prompt_name}_demos.json")
    try:
        with open(demo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("demonstrations", [])
    except FileNotFoundError:
        return []  # No demonstrations file - that's okay
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

def extract_score(text):
    """
    Extract score from model response.
    Prompts expect JSON with "final_score" key.
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

def format_messages(prompt_data, context, q, a, demos=None):
    """
    Format messages with optional demonstrations inserted.
    """
    # Default fallback
    system_role = "You are a clinical auditor."
    user_text = f"Context:\n{context}\n\nQuestion: {q}\nAnswer: {a}\n\nRate factual accuracy on a 1-5 scale."

    if prompt_data:
        # Handle empty system_role
        system_role = prompt_data.get("system_role", system_role)
        if system_role == "":
            system_role = "You are a clinical auditor."  # Fallback if empty
        
        if "instruction_template" in prompt_data:
            try:
                # Try with context first (for RAG prompts that might have it)
                user_text = prompt_data["instruction_template"].format(
                    context=context, question=q, answer=a
                )
            except KeyError:
                # Fallback: prompts without context placeholder (like def_only and cot_v4)
                try:
                    user_text = prompt_data["instruction_template"].format(
                        question=q, answer=a
                    )
                except KeyError as e:
                    print(f"⚠️ Prompt template key error: {e}")
                    # Keep the default fallback user_text
            
            # Insert demonstrations if provided (after successful formatting)
            if demos:
                demo_text = format_demonstrations(demos)
                # Insert demonstrations before RESPONSE FORMAT section
                if "### RESPONSE FORMAT" in user_text:
                    user_text = user_text.replace("### RESPONSE FORMAT", demo_text + "\n### RESPONSE FORMAT")
                elif "### OUTPUT FORMAT" in user_text:
                    user_text = user_text.replace("### OUTPUT FORMAT", demo_text + "\n### OUTPUT FORMAT")
                else:
                    # If no format section found, append at the end
                    user_text += demo_text
                        
    # If system_role is empty, we can omit it or use minimal
    if system_role == "":
        return [{"role": "user", "content": user_text}]
    else:
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
    # 1. Load Data (once, shared across all variations)
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    if TEST_MODE:
        print("🔬 TEST MODE ON: Processing only first 5 rows.")
        df = df.head(5)

    # 2. OUTER LOOP: PROMPT VARIATIONS (with demonstrations)
    for variation_name, variation_config in PROMPT_VARIATIONS.items():
        BASE_PROMPTS_DIR = variation_config["base_dir"]  # Base prompts without demos
        OUTPUT_DIR = variation_config["output"]
        
        print(f"\n" + "="*80)
        print(f"📝 PROCESSING VARIATION: {variation_name.upper()}")
        print(f"   Base Prompts: {BASE_PROMPTS_DIR}")
        print(f"   Demonstrations: {DEMONSTRATIONS_DIR}")
        print(f"   Output: {OUTPUT_DIR}")
        print("="*80)
        
        # Check if base prompt directory exists
        if not os.path.exists(BASE_PROMPTS_DIR):
            print(f"⚠️ Base prompt directory does not exist: {BASE_PROMPTS_DIR}")
            print(f"   Skipping variation: {variation_name}")
            continue
        
        # Check if demonstrations directory exists
        if not os.path.exists(DEMONSTRATIONS_DIR):
            print(f"⚠️ Demonstrations directory does not exist: {DEMONSTRATIONS_DIR}")
            print(f"   Will proceed without demonstrations.")
        
        # 0. Setup output directory
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        prompt_files = discover_prompts(BASE_PROMPTS_DIR, EXCLUDED_PROMPTS)
        print(f"📋 Found {len(prompt_files)} prompts to test.")
        
        if len(prompt_files) == 0:
            print(f"⚠️ No prompts found in {BASE_PROMPTS_DIR}. Skipping this variation.")
            continue

        # 3. Initialize Model/Client
        if USE_VLLM:
            # vLLM mode: Connect to vLLM server
            print(f"\n" + "="*60)
            print(f"🤖 CONNECTING TO VLLM SERVER: {VLLM_API_BASE}")
            print(f"   Model: {VLLM_MODEL_NAME}")
            print("="*60)
            
            try:
                client = OpenAI(
                    base_url=VLLM_API_BASE,
                    api_key="EMPTY"  # Required but unused for local vLLM
                )
                # Test connection
                models = client.models.list()
                print(f"✅ Connected to vLLM server. Available models: {[m.id for m in models.data]}")
                model_short = VLLM_MODEL_NAME.replace("-", "_").replace(".", "")
            except Exception as e:
                print(f"❌ Failed to connect to vLLM server: {e}")
                print(f"   Make sure vLLM server is running on {VLLM_API_BASE}")
                print(f"   Skipping variation: {variation_name}")
                continue
            
            # Process all prompts with vLLM
            for prompt_file in prompt_files:
                prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
                output_filename = f"QA_{prompt_name}_{model_short}_{variation_name}_NO_RAG_1224.json"
                output_path = os.path.join(OUTPUT_DIR, output_filename)

                if os.path.exists(output_path):
                    print(f"⏩ Skipping {output_filename} (Already exists)")
                    continue

                print(f"   📝 Running Prompt: {prompt_name}...")
                prompt_data = load_prompt(prompt_file)
                
                if prompt_data is None:
                    print(f"   ⚠️ Skipping {prompt_name} (Failed to load prompt)")
                    continue
                
                # Load demonstrations for this prompt
                demos = load_demonstrations(prompt_name)
                if demos:
                    print(f"   📚 Loaded {len(demos)} demonstrations for {prompt_name}")
                else:
                    print(f"   ⚠️ No demonstrations found for {prompt_name} (will proceed without demos)")
                
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
                    question_num = row.get("question_num", None)  # Preserve question_num from CSV
                    q, a = str(row["question"]), str(row["answer"])
                    
                    # Get human score using the mapped column name
                    if human_score_column:
                        human_score = row.get(human_score_column, None)
                    else:
                        human_score = None

                    # DUMMY CONTEXT (NO RAG)
                    ctx = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

                    # Format messages with demonstrations
                    messages = format_messages(prompt_data, ctx, q, a, demos=demos)
                    
                    # vLLM Inference via OpenAI API
                    try:
                        resp = client.chat.completions.create(
                            model=VLLM_MODEL_NAME,
                            messages=messages,
                            temperature=0.0,
                            max_tokens=MAX_NEW_TOKENS,
                        )
                        resp_text = resp.choices[0].message.content
                    except Exception as e:
                        print(f"⚠️ vLLM API error for row {idx}: {e}")
                        resp_text = ""
                    
                    results.append({
                        "question_num": question_num,
                        "trial_num": trial,
                        "question": q,
                        "chatbot_answer": a,
                        "human_score": human_score,
                        "retrieved_context": "NO_RAG_CONTROL",
                        "ai_reasoning": resp_text,
                        "ai_score": extract_score(resp_text),
                        "prompt_file": prompt_name,
                        "prompt_variation": variation_name,
                        "model": model_short
                    })

                # Save results atomically for this prompt/model combo
                if save_atomic(results, output_path):
                    print(f"      ✅ Saved {output_filename}")
                else:
                    print(f"      ❌ Failed to save {output_filename}")

        else:
            # HuggingFace Transformers mode
            for model_path in MODELS_LIST:
                print(f"\n" + "="*60)
                print(f"🤖 LOADING MODEL: {model_path}")
                print("="*60)

                # Load Model & Tokenizer
                try:
                    tokenizer = AutoTokenizer.from_pretrained(model_path)
                    model = AutoModelForCausalLM.from_pretrained(
                        model_path,
                        dtype=torch.bfloat16,
                        device_map="auto"
                    )
                except Exception as e:
                    print(f"❌ Failed to load {model_path}: {e}")
                    continue # Skip to next model

                model_short = get_model_short_name(model_path)

                # 4. INNER LOOP: PROMPTS
                for prompt_file in prompt_files:
                    prompt_name = os.path.splitext(os.path.basename(prompt_file))[0]
                    output_filename = f"QA_{prompt_name}_{model_short}_{variation_name}_NO_RAG.json"
                    output_path = os.path.join(OUTPUT_DIR, output_filename)

                    if os.path.exists(output_path):
                        print(f"⏩ Skipping {output_filename} (Already exists)")
                        continue

                    print(f"   📝 Running Prompt: {prompt_name}...")
                    prompt_data = load_prompt(prompt_file)
                    
                    if prompt_data is None:
                        print(f"   ⚠️ Skipping {prompt_name} (Failed to load prompt)")
                        continue
                    
                    # Load demonstrations for this prompt
                    demos = load_demonstrations(prompt_name)
                    if demos:
                        print(f"   📚 Loaded {len(demos)} demonstrations for {prompt_name}")
                    else:
                        print(f"   ⚠️ No demonstrations found for {prompt_name} (will proceed without demos)")
                    
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
                        question_num = row.get("question_num", None)  # Preserve question_num from CSV
                        q, a = str(row["question"]), str(row["answer"])
                        
                        # Get human score using the mapped column name
                        if human_score_column:
                            human_score = row.get(human_score_column, None)
                        else:
                            human_score = None

                        # DUMMY CONTEXT (NO RAG)
                        ctx = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

                        # Format messages with demonstrations
                        messages = format_messages(prompt_data, ctx, q, a, demos=demos)
                        
                        # HuggingFace Inference
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
                        
                        resp_text = tokenizer.decode(output[0][input_len:], skip_special_tokens=True)
                        
                        results.append({
                            "question_num": question_num,
                            "trial_num": trial,
                            "question": q,
                            "chatbot_answer": a,
                            "human_score": human_score,
                            "retrieved_context": "NO_RAG_CONTROL",
                            "ai_reasoning": resp_text,
                            "ai_score": extract_score(resp_text),
                            "prompt_file": prompt_name,
                            "prompt_variation": variation_name,
                            "model": model_short
                        })

                    # Save results atomically for this prompt/model combo
                    if save_atomic(results, output_path):
                        print(f"      ✅ Saved {output_filename}")
                    else:
                        print(f"      ❌ Failed to save {output_filename}")

                # 5. Cleanup after Model is done with ALL prompts
                print(f"🧹 Unloading {model_short}...")
                del model
                del tokenizer
                torch.cuda.empty_cache()
                gc.collect()

    print("\n✅ ALL VARIATIONS COMPLETE.")

if __name__ == "__main__":
    main()