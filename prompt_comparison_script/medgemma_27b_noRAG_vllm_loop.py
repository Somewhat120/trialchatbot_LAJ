import os
import glob
import json
import pandas as pd
import torch
import re
import gc
import tempfile
import argparse
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from transformers import AutoTokenizer, AutoModelForCausalLM
from openai import OpenAI  # For vLLM support

# ==========================================
# CONFIGURATION
# ==========================================
# Prompt variations to test
PROMPT_VARIATIONS = {
    "baseline": {
        "dir": "prompt_comparison_folder/baseline/",
        "output": "LAJ_score_NO_RAG_baseline/"
    },
    "def_only": {
        "dir": "prompt_comparison_folder/prompts_def_only/",
        "output": "LAJ_score_NO_RAG_def_only/"
    },
    "def_cot": {
        "dir": "prompt_comparison_folder/prompts_cot/",  # Current version
        "output": "LAJ_score_NO_RAG_def_cot/"
    }
}

EXCLUDED_PROMPTS = ["5_acc_prompt.json"] 
INPUT_CSV = "data_preparation/raters.csv"

MODELS_LIST = [
    "models/medgemma-27b-text-it"
]

# vLLM Configuration
USE_VLLM = True  # Set to False to use direct HuggingFace loading
VLLM_API_BASE = os.getenv("VLLM_API_BASE", "http://localhost:8000/v1")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "medgemma-27b")  # Must match --served-model-name from vLLM server

TEST_MODE = False # Set to True for a quick debug run
MAX_NEW_TOKENS = 768
REPETITION_PENALTY = 1.0

# Concurrency configuration - optimized for H200 GPUs with higher throughput
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "12"))  # Increased from 8 to match --max-num-seqs 12

# Mapping from prompt filename (without extension) to CSV column name for human scores
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
    Prompts expect JSON with "final_score" key.
    Returns -1 if parsing fails (so you can distinguish from actual score 0).
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
    
    return -1  # Changed from 0 to -1 to indicate parsing failure

def format_messages(prompt_data, context, q, a):
    # Default fallback - generic, not specific to any evaluation type
    system_role = "You are a clinical auditor."
    user_text = f"Context:\n{context}\n\nQuestion: {q}\nAnswer: {a}\n\nPlease evaluate and provide a score from 1-5."

    if prompt_data:
        # Handle empty system_role
        system_role = prompt_data.get("system_role", system_role)
        if system_role == "":
            system_role = "You are a clinical auditor."  # Fallback if empty
        
        if "instruction_template" in prompt_data:
            try:
                # Try formatting with context first (for RAG prompts)
                try:
                    user_text = prompt_data["instruction_template"].format(
                        context=context, question=q, answer=a
                    )
                except KeyError:
                    # If context placeholder doesn't exist, try without it (for NO-RAG prompts)
                    user_text = prompt_data["instruction_template"].format(
                        question=q, answer=a
                    )
            except KeyError as e:
                print(f"⚠️ Prompt template key error: {e}")
                # Keep the generic fallback if formatting fails
    
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
# ARGUMENT PARSER
# ==========================================
def parse_arguments():
    parser = argparse.ArgumentParser(description="MedGemma 27B NO-RAG evaluation with vLLM")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output files (default: skip existing files)"
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=None,
        help=f"Number of concurrent vLLM workers (default: {MAX_WORKERS}, or set MAX_WORKERS env var)"
    )
    return parser.parse_args()

# ==========================================
# CONCURRENT PROCESSING
# ==========================================
def process_single_row(row_data):
    """
    Process a single row: vLLM call.
    This function is designed to be called in parallel.
    
    Args:
        row_data: Tuple of (row, client, prompt_data, human_score_column, VLLM_MODEL_NAME, MAX_NEW_TOKENS)
    
    Returns:
        dict: Result dictionary
    """
    row, client, prompt_data, human_score_column, model_name, max_tokens = row_data
    
    # Handle different CSV column names
    # raters.csv uses: trial_num, question, answer, question_num
    # Other CSVs might use: nct_id, questions, response
    trial = str(row.get("trial_num", row.get("nct_id", "UNKNOWN")))
    q = str(row.get("question", row.get("questions", "")))
    a = str(row.get("answer", row.get("response", "")))
    
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
            "ai_reasoning": "",
            "ai_score": -1
        }
    
    # Extract question_num from CSV row (with fallback)
    question_num = row.get("question_num", None)
    if question_num is not None:
        try:
            question_num = int(float(question_num)) if pd.notna(question_num) else None
        except (ValueError, TypeError):
            question_num = None
    
    # Get human score using the mapped column name (None is OK - we still want AI scores)
    if human_score_column:
        human_score = row.get(human_score_column, None)
    else:
        human_score = None

    # DUMMY CONTEXT (NO RAG)
    ctx = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

    messages = format_messages(prompt_data, ctx, q, a)
    
    # vLLM Inference via OpenAI API
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0,
            top_p=1.0,  # Added for deterministic output
            seed=42,    # Added for deterministic output
            max_tokens=max_tokens,
        )
        resp_text = resp.choices[0].message.content
        ai_score = extract_score(resp_text)
    except Exception as e:
        return {
            "status": "error",
            "reason": f"vllm_error: {e}",
            "trial_num": trial,
            "question_num": question_num,
            "question": q,
            "chatbot_answer": a,
            "human_score": human_score,
            "retrieved_context": "NO_RAG_CONTROL",
            "ai_reasoning": f"Error: {str(e)}",  # Include error in ai_reasoning
            "ai_score": -1
        }
    
    return {
        "status": "success",
        "trial_num": trial,
        "question_num": question_num,
        "question": q,
        "chatbot_answer": a,
        "human_score": human_score,
        "retrieved_context": "NO_RAG_CONTROL",
        "ai_reasoning": resp_text,
        "ai_score": ai_score
    }

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    args = parse_arguments()
    # 1. Load Data (once, shared across all variations)
    print(f"📖 Loading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    if TEST_MODE:
        print("🔬 TEST MODE ON: Processing only first 1 row.")
        df = df.head(1)

    # 2. OUTER LOOP: PROMPT VARIATIONS
    for variation_name, variation_config in PROMPT_VARIATIONS.items():
        PROMPTS_DIR = variation_config["dir"]
        OUTPUT_DIR = variation_config["output"]
        
        print(f"\n" + "="*80)
        print(f"📝 PROCESSING VARIATION: {variation_name.upper()}")
        print(f"   Prompts: {PROMPTS_DIR}")
        print(f"   Output: {OUTPUT_DIR}")
        print("="*80)
        
        # Check if prompt directory exists
        if not os.path.exists(PROMPTS_DIR):
            print(f"⚠️ Prompt directory does not exist: {PROMPTS_DIR}")
            print(f"   Skipping variation: {variation_name}")
            continue
        
        # 0. Setup output directory
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        prompt_files = discover_prompts(PROMPTS_DIR, EXCLUDED_PROMPTS)
        print(f"📋 Found {len(prompt_files)} prompts to test.")
        
        if len(prompt_files) == 0:
            print(f"⚠️ No prompts found in {PROMPTS_DIR}. Skipping this variation.")
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
                output_filename = f"QA_{prompt_name}_{model_short}_{variation_name}_NO_RAG_raters.json"
# Example: QA_1_critical_harm_medgemma_27b_baseline_NO_RAG_raters.json
                output_path = os.path.join(OUTPUT_DIR, output_filename)

                if os.path.exists(output_path) and not args.force:
                    print(f"⏩ Skipping {output_filename} (Already exists, use --force to overwrite)")
                    continue
                elif os.path.exists(output_path) and args.force:
                    print(f"🔄 Overwriting {output_filename} (--force flag set)")

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
                
                # Prepare row data for concurrent processing
                row_data_list = [
                    (row, client, prompt_data, human_score_column, VLLM_MODEL_NAME, MAX_NEW_TOKENS)
                    for _, row in df.iterrows()
                ]
                
                # Use provided max_workers or default
                workers = args.max_workers if args.max_workers is not None else MAX_WORKERS
                
                print(f"   🚀 Processing {len(row_data_list)} rows with {workers} concurrent workers...")
                
                # Process rows concurrently
                error_count = 0
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    # Submit all tasks
                    future_to_row = {
                        executor.submit(process_single_row, row_data): row_data[0]
                        for row_data in row_data_list
                    }
                    
                    # Process completed tasks with progress bar
                    for future in tqdm(as_completed(future_to_row), total=len(future_to_row), ncols=80, desc="Processing", leave=False):
                        try:
                            result = future.result()
                            # Save ALL results (success and errors) - user wants AI scores even if human scores missing
                            if result["status"] == "success":
                                results.append({
                                    "trial_num": result.get("trial_num", result.get("trial", "UNKNOWN")),
                                    "question_num": result.get("question_num", None),
                                    "question": result.get("question", ""),
                                    "chatbot_answer": result.get("chatbot_answer", ""),
                                    "human_score": result.get("human_score", None),  # None is OK
                                    "retrieved_context": result.get("retrieved_context", "NO_RAG_CONTROL"),
                                    "ai_reasoning": result.get("ai_reasoning", ""),
                                    "ai_score": result.get("ai_score", -1),
                                    "prompt_file": prompt_name,
                                    "prompt_variation": variation_name,
                                    "model": model_short
                                })
                            else:
                                # Save error results too so we can see what went wrong
                                error_count += 1
                                results.append({
                                    "trial_num": result.get("trial_num", result.get("trial", "UNKNOWN")),
                                    "question_num": result.get("question_num", None),
                                    "question": result.get("question", ""),
                                    "chatbot_answer": result.get("chatbot_answer", ""),
                                    "human_score": result.get("human_score", None),
                                    "retrieved_context": result.get("retrieved_context", "NO_RAG_CONTROL"),
                                    "ai_reasoning": result.get("ai_reasoning", ""),
                                    "ai_score": result.get("ai_score", -1),
                                    "status": result.get("status", "error"),
                                    "error_reason": result.get("reason", "unknown"),
                                    "prompt_file": prompt_name,
                                    "prompt_variation": variation_name,
                                    "model": model_short
                                })
                                if error_count <= 5:  # Print first 5 errors
                                    print(f"\n⚠️ Error processing {result.get('trial_num', result.get('trial', 'unknown'))}: {result.get('reason', 'unknown')}")
                        except Exception as e:
                            error_count += 1
                            if error_count <= 5:
                                print(f"\n⚠️ Unexpected error: {e}")
                            # Try to save partial result if possible
                            try:
                                # Get row data to extract basic info
                                row_data = future_to_row.get(future, None)
                                if row_data:
                                    row = row_data[0] if isinstance(row_data, tuple) else None
                                    if row is not None:
                                        results.append({
                                            "trial_num": str(row.get("trial_num", row.get("nct_id", "UNKNOWN"))),
                                            "question_num": row.get("question_num", None),
                                            "question": str(row.get("question", row.get("questions", ""))),
                                            "chatbot_answer": str(row.get("answer", row.get("response", ""))),
                                            "human_score": None,
                                            "retrieved_context": "NO_RAG_CONTROL",
                                            "ai_reasoning": "",
                                            "ai_score": -1,
                                            "status": "exception",
                                            "error_reason": str(e),
                                            "prompt_file": prompt_name,
                                            "prompt_variation": variation_name,
                                            "model": model_short
                                        })
                            except:
                                pass  # If we can't extract info, skip
                
                if error_count > 5:
                    print(f"\n⚠️ Total errors: {error_count} (showing first 5)")
                elif error_count > 0:
                    print(f"\n⚠️ Total errors: {error_count}")

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
                    output_filename = f"QA_{prompt_name}_{model_short}_{variation_name}_NO_RAG_raters.json"
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
                        
                        # Extract question_num from CSV row (with fallback)
                        question_num = row.get("question_num", None)
                        if question_num is not None:
                            try:
                                question_num = int(float(question_num)) if pd.notna(question_num) else None
                            except (ValueError, TypeError):
                                question_num = None
                        
                        # Get human score using the mapped column name
                        if human_score_column:
                            human_score = row.get(human_score_column, None)
                        else:
                            human_score = None

                        # DUMMY CONTEXT (NO RAG)
                        ctx = "[NO SOURCE TEXT PROVIDED. JUDGE BASED ON GENERAL MEDICAL KNOWLEDGE ONLY.]"

                        messages = format_messages(prompt_data, ctx, q, a)
                        
                        # HuggingFace Inference
                        inputs = tokenizer.apply_chat_template(
                            messages, 
                            add_generation_prompt=True, 
                            tokenize=True, 
                            return_dict=True, 
                            return_tensors="pt"
                        )
                        # HuggingFace Inference section (lines 526-544)
                        inputs = tokenizer.apply_chat_template(
                            messages, 
                            add_generation_prompt=True, 
                            tokenize=True, 
                            return_dict=True, 
                            return_tensors="pt"
                        ).to(model.device)  # Changed: use .to() directly instead of dict comprehension

                        input_len = inputs["input_ids"].shape[-1]

                        with torch.inference_mode():  # Changed: use inference_mode() instead of no_grad()
                            output = model.generate(
                                **inputs, 
                                max_new_tokens=MAX_NEW_TOKENS, 
                                do_sample=False,
                                repetition_penalty=REPETITION_PENALTY  # Keep this - it's helpful
                            )
                            generation = output[0][input_len:]  # Changed: slice before decode

                        resp_text = tokenizer.decode(generation, skip_special_tokens=True)                        
                        results.append({
                            "trial_num": trial,
                            "question_num": question_num,
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