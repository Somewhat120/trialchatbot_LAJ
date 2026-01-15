import pandas as pd
import json
import os
import glob
import re
from sklearn.metrics import confusion_matrix, accuracy_score, cohen_kappa_score, mean_absolute_error

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
INPUT_FILE = None  # Set to None to use INPUT_ROOT_DIRS instead
INPUT_ROOT_DIRS = [
    r"LAJ_score_noRAG_prompt_comp",  # Old structure with add_round1
    r"LAJ_score_MEDGEMMA_RAG_short", 
    r"LAJ_score_QWEN_RAG_short", # New structure without add_round1
    r"LAJ_score_RAG_prompt_comp"  # 3-level and 5-level structures
]
OUTPUT_DIR = r"prompt_comp_results_Likert_add_round1"

# 🔍 DEBUG SETTINGS
MIN_ROWS_REQUIRED = 1  
PRINT_DEBUG = True     

# Regex - Updated to handle criteria in middle of filename
BINARY_CRITERIA_REGEX = r"^(?:QA_)?([1-3])_"
# Updated: matches criteria number in middle (e.g., ...__5_factual_acc__...) or at start
LIKERT_CRITERIA_REGEX = r"(?:QA_)?([4-9])_(.+?)(?:\.json|\.jsonl|$)|__([4-9])_(.+?)__"

# ==========================================
# 📊 METRICS FUNCTION
# ==========================================
def load_json_or_jsonl(file_path):
    """Load either JSON array or JSONL (one JSON object per line)"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            if file_path.endswith('.jsonl'):
                # JSONL format: one JSON object per line
                data = []
                for line in f:
                    line = line.strip()
                    if line:
                        data.append(json.loads(line))
                return data
            else:
                # JSON format: single JSON array
                return json.load(f)
    except Exception as e:
        if PRINT_DEBUG: print(f"   ❌ FAIL: Error loading file - {str(e)}")
        return None

def calculate_likert_metrics(file_path):
    try:
        data = load_json_or_jsonl(file_path)
        
        if data is None:
            return None
        
        # Check if list is empty
        if not isinstance(data, list) or len(data) == 0:
            if PRINT_DEBUG: print(f"   ❌ FAIL: File is empty or not a JSON list")
            return None

        df = pd.DataFrame(data)

        # Check for required keys
        if 'human_score' not in df.columns or 'ai_score' not in df.columns:
            if PRINT_DEBUG: print(f"   ❌ FAIL: Missing 'human_score' or 'ai_score' keys")
            return None

        # Clean Data
        cols = ['human_score', 'ai_score']
        df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
        
        # Check valid rows
        valid_df = df.dropna(subset=cols)
        # Filter for 1-5 range
        in_range_df = valid_df[valid_df['ai_score'].between(1, 5) & valid_df['human_score'].between(1, 5)]
        
        if len(in_range_df) < MIN_ROWS_REQUIRED:
            if PRINT_DEBUG: 
                print(f"   ❌ FAIL: Only {len(in_range_df)} valid rows found (Needs {MIN_ROWS_REQUIRED}).")
            return None

        # Calculate Metrics
        y_true = in_range_df['human_score'].astype(int)
        y_pred = in_range_df['ai_score'].astype(int)

        pearson = y_true.corr(y_pred, method='pearson')
        spearman = y_true.corr(y_pred, method='spearman')
        acc_exact = accuracy_score(y_true, y_pred)
        acc_within_1 = (abs(y_true - y_pred) <= 1).mean()
        
        # --- KAPPA CALCULATIONS ---
        # 1. Standard (Unweighted): Treats 1vs2 error same as 1vs5 error
        kappa_std = cohen_kappa_score(y_true, y_pred, weights=None)
        # 2. Linear: Penalty is proportional (1vs5 is 4x worse than 1vs2)
        kappa_linear = cohen_kappa_score(y_true, y_pred, weights='linear')
        # 3. Quadratic: Penalty is squared (1vs5 is 16x worse than 1vs2)
        kappa_quad = cohen_kappa_score(y_true, y_pred, weights='quadratic')
        
        mae = mean_absolute_error(y_true, y_pred)
        cm = confusion_matrix(y_true, y_pred, labels=[1,2,3,4,5])

        return {
            "n": int(len(in_range_df)),
            "correlations": { "pearson": float(pearson), "spearman": float(spearman) },
            "accuracy": { "exact_match": float(acc_exact), "within_1_point": float(acc_within_1) },
            "reliability": { 
                "kappa_standard": float(kappa_std),   # <--- ADDED
                "kappa_linear": float(kappa_linear), 
                "kappa_quadratic": float(kappa_quad) 
            },
            "error": { "mae": float(mae) },
            "confusion_matrix": cm.tolist()
        }
    except Exception as e:
        if PRINT_DEBUG: print(f"   ❌ FAIL: Python Error - {str(e)}")
        return None

# ==========================================
# 📂 METADATA LOGIC
# ==========================================
def extract_metadata(file_path):
    filename = os.path.basename(file_path)
    
    if re.search(BINARY_CRITERIA_REGEX, filename): 
        return None

    # Try matching criteria at start first
    match = re.search(r"(?:QA_)?([4-9])_(.+?)(?:\.json|\.jsonl|$)", filename)
    if match:
        crit_num = match.group(1)
        full_crit_name = match.group(2)
    else:
        # Try matching criteria in middle (e.g., ...__5_factual_acc__...)
        match = re.search(r"__([4-9])_(.+?)__", filename)
        if match:
            crit_num = match.group(1)
            full_crit_name = match.group(2)
        else:
            if PRINT_DEBUG: print(f"   ⚠️ SKIP: Filename didn't match Regex (starts with 4-9 or has __4-9__): {filename}")
            return None
    
    parts = full_crit_name.split("_")
    short_crit_name = "_".join(parts[:2]) if len(parts) > 1 else full_crit_name
    criterion_key = f"{crit_num}_{short_crit_name}"

    path_parts = os.path.normpath(file_path).split(os.sep)
    
    # Handle different path structures:
    # Old: root/Model/Strategy_Folder/add_round1/file.json → path_parts[-4] = model, path_parts[-3] = strategy
    # New: root/Strategy_Folder/file.jsonl → path_parts[-2] = strategy (2 levels)
    if len(path_parts) >= 4 and path_parts[-2] == "add_round1":
        # Old structure: root/Model/Strategy_Folder/add_round1/file.json
        strategy_folder = path_parts[-3]  # e.g., LAJ_score_GPT5_oracle_baseline
        model_folder = path_parts[-4]     # e.g., GPT5
    elif len(path_parts) >= 3:
        # New structure: root/Strategy_Folder/file.jsonl
        # e.g., LAJ_score_MEDGEMMA_RAG_short/LAJ_score_MEDGEMMA_RAG_baseline/file.jsonl
        strategy_folder = path_parts[-2]  # e.g., LAJ_score_MEDGEMMA_RAG_baseline
        # Extract model from strategy folder name
        if "MEDGEMMA" in strategy_folder.upper():
            model_folder = "medgemma"
        elif "GPT5" in strategy_folder.upper() or "GPT" in strategy_folder.upper():
            model_folder = "GPT5"
        elif "QWEN" in strategy_folder.upper():
            model_folder = "qwen"
        else:
            # Try to extract from parent folder
            if len(path_parts) >= 3:
                parent_folder = path_parts[-3]
                if "MEDGEMMA" in parent_folder.upper():
                    model_folder = "medgemma"
                elif "GPT" in parent_folder.upper():
                    model_folder = "GPT5"
                elif "QWEN" in parent_folder.upper():
                    model_folder = "qwen"
                else:
                    model_folder = "Unknown_Model"
            else:
                model_folder = "Unknown_Model"
    else:
        model_folder = "Unknown_Model"
        strategy_folder = None
    
    if strategy_folder:
        base_strategy = strategy_folder.replace(f"LAJ_score_{model_folder}_", "").replace("LAJ_score_", "")
        
        # Extract file tag - handle both old and new naming patterns
        if "__" in filename:
            # New pattern: raters_...__5_factual_acc__medgemma_27b__...
            # Extract everything before the first __
            file_tag = filename.split("__")[0]
        else:
            # Old pattern: QA_5_factual_acc_medgemma_27b_baseline_NO_RAG_1224.json
            prefix_to_remove = f"{crit_num}_{short_crit_name}"
            file_tag = filename.replace(prefix_to_remove, "").replace(".json", "").replace(".jsonl", "")
        
        file_tag = re.sub(r"^_", "", file_tag)
        
        # Detect demo vs no_demo from filename
        demo_tag = "demo" if "_demo_" in filename.lower() else "no_demo"
        
        unique_strategy_key = f"{base_strategy}__{demo_tag}__{file_tag}"
    else:
        model_folder = "Unknown_Model"
        demo_tag = "demo" if "_demo_" in filename.lower() else "no_demo"
        unique_strategy_key = f"{demo_tag}__{filename.replace('.json', '').replace('.jsonl', '')}"

    return criterion_key, model_folder, unique_strategy_key

# ==========================================
# 🚀 MAIN EXECUTION
# ==========================================
def main():
    print(f"🚀 Starting DIAGNOSTIC Scan (Likert 1-5)...")
    
    files_to_process = []

    if INPUT_FILE and os.path.exists(INPUT_FILE):
        files_to_process.append(INPUT_FILE)
    
    if INPUT_ROOT_DIRS:
        for root_dir in INPUT_ROOT_DIRS:
            if os.path.exists(root_dir):
                # Search for both .json and .jsonl files
                # First try add_round1 subdirectories (old structure)
                found_old = glob.glob(os.path.join(root_dir, "**", "add_round1", "*.json"), recursive=True)
                files_to_process.extend(found_old)
                
                # Then try direct subdirectories (new structure)
                found_new = glob.glob(os.path.join(root_dir, "**", "*.jsonl"), recursive=True)
                # Also search for .json files not in add_round1
                found_new_json = glob.glob(os.path.join(root_dir, "**", "*.json"), recursive=True)
                # Filter out files already in add_round1
                found_new_json = [f for f in found_new_json if "add_round1" not in f]
                files_to_process.extend(found_new)
                files_to_process.extend(found_new_json)

    print(f"📂 Found {len(files_to_process)} total JSON/JSONL files. Checking them now...\n")

    aggregated_data = {}
    processed_count = 0

    for f in files_to_process:
        meta = extract_metadata(f)
        if not meta: continue 
        
        crit, model, strategy = meta
        
        print(f"🔎 Checking: {os.path.basename(f)}")
        metrics = calculate_likert_metrics(f)
        
        if not metrics: 
            continue
        
        print(f"   ✅ SUCCESS! Rows: {metrics['n']}")

        if crit not in aggregated_data: aggregated_data[crit] = {}
        if model not in aggregated_data[crit]: aggregated_data[crit][model] = {}
        
        aggregated_data[crit][model][strategy] = metrics
        processed_count += 1

    print(f"\n✅ Aggregated {processed_count} files.")
    
    for crit, models in aggregated_data.items():
        crit_dir = os.path.join(OUTPUT_DIR, crit)
        os.makedirs(crit_dir, exist_ok=True)
        for model, strategies in models.items():
            output_file = os.path.join(crit_dir, f"{model}.json")
            with open(output_file, 'w', encoding='utf-8') as out_f:
                json.dump(strategies, out_f, indent=4)
            print(f"   -> Saved: {output_file}")

if __name__ == "__main__":
    main()