import pandas as pd
import json
import os
import glob
import re
import warnings
from sklearn.metrics import f1_score, matthews_corrcoef, accuracy_score, confusion_matrix

# Suppress sklearn warnings
warnings.filterwarnings('ignore', category=UserWarning, module='sklearn')

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
INPUT_ROOT_DIR = r"LAJ_score_noRAG_prompt_comp"
OUTPUT_DIR = r"prompt_comp_results_add_round1"

# STRICT FILTER: Only process these IDs
# Files starting with 4, 5, 6, 7 will be ignored.
TARGET_IDS = ["1", "2", "3"]

CRITERIA_MAP = {
    "1": "1_critical_harm",
    "2": "2_individualized_advice",
    "3": "3_persuation"
}

# ==========================================
# METRICS FUNCTION
# ==========================================
def calculate_metrics(df):
    try:
        y_true = df['human_score']
        y_pred = df['ai_score']
        
        # Force 2x2 Matrix labels=[0, 1]
        cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
        tn, fp, fn, tp = cm.ravel()
        
        specificity = float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0
        sensitivity = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        
        return {
            "n": int(len(df)),
            "accuracy": float(accuracy_score(y_true, y_pred)),
            "f1": float(f1_score(y_true, y_pred, average='binary', zero_division=0)),
            "mcc": float(matthews_corrcoef(y_true, y_pred)),
            "precision": precision,
            "specificity": specificity,
            "sensitivity_recall": sensitivity,
            "confusion_matrix": {"TN": int(tn), "FP": int(fp), "FN": int(fn), "TP": int(tp)}
        }
    except Exception as e:
        return None

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    print(f"🚀 Scanning Root: {INPUT_ROOT_DIR}")
    
    # Filter to only files in 'add_round1' subdirectories
    all_files = glob.glob(os.path.join(INPUT_ROOT_DIR, "**", "add_round1", "*.json"), recursive=True)
    aggregated_data = {}
    
    print(f"📂 Found {len(all_files)} total files.")
    print(f"🎯 Filtering for Criteria IDs: {TARGET_IDS}\n")
    
    count = 0
    ignored_count = 0
    skipped_error_count = 0

    for file_path in all_files:
        filename = os.path.basename(file_path)

        # 1. EXTRACT ID FROM FILENAME
        # Regex looks for "QA_" (optional) -> ID (1-9) -> Separator
        match_file = re.search(r"(?:QA_|^)([0-9])(?:[_\-\.\s]|$)", filename)
        
        if not match_file:
            # Fallback: check prompt_file inside JSON later, but usually filename is enough
            pass
        else:
            crit_id = match_file.group(1)
            
            # --- STRICT FILTER ---
            if crit_id not in TARGET_IDS:
                # Silently ignore 4, 5, 6, 7...
                ignored_count += 1
                continue
            
            criterion = CRITERIA_MAP.get(crit_id, f"Criterion_{crit_id}")

        # 2. LOAD DATA
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            print(f"❌ [ERROR] {filename}: JSON Load Failed")
            skipped_error_count += 1
            continue

        if not isinstance(data, list) or not data:
            skipped_error_count += 1
            continue

        # 3. VERIFY METADATA (Double Check)
        first_row = data[0]
        prompt_file = first_row.get("prompt_file", "")
        
        # If filename didn't match, try matching internal prompt_file
        if 'crit_id' not in locals(): 
            match_internal = re.search(r"^([0-9])", prompt_file)
            if match_internal:
                crit_id = match_internal.group(1)
                if crit_id not in TARGET_IDS:
                    ignored_count += 1
                    continue
                criterion = CRITERIA_MAP.get(crit_id, f"Criterion_{crit_id}")
            else:
                skipped_error_count += 1
                continue

        # Extract model and strategy from JSON or fallback to path
        path_parts = os.path.normpath(file_path).split(os.sep)
        
        # Extract model - handle both structures:
        # Old: root/Model/Strategy_Folder/file.json → path_parts[-3] = model
        # New: root/Model/Strategy_Folder/add_round1/file.json → path_parts[-4] = model
        model = first_row.get("model", None)
        if not model:
            # Check if we have the add_round1 level (4+ parts from end)
            if len(path_parts) >= 4:
                # New structure: root/Model/Strategy_Folder/add_round1/file.json
                model = path_parts[-4]
            elif len(path_parts) >= 3:
                # Old structure: root/Model/Strategy_Folder/file.json
                model = path_parts[-3]
            elif len(path_parts) >= 2:
                model = path_parts[-2]
            else:
                model = "Unknown_Model"
        
        # Extract strategy - handle both structures:
        # Old: root/Model/Strategy_Folder/file.json → path_parts[-2] = strategy
        # New: root/Model/Strategy_Folder/add_round1/file.json → path_parts[-3] = strategy
        strategy = first_row.get("prompt_variation", None)
        if not strategy:
            # Check if we have the add_round1 level
            if len(path_parts) >= 3:
                # New structure: strategy folder is path_parts[-3]
                strategy_folder = path_parts[-3]  # e.g., LAJ_score_GPT5_baseline
            elif len(path_parts) >= 2:
                # Old structure: strategy folder is path_parts[-2]
                strategy_folder = path_parts[-2]
            else:
                strategy = "Unknown_Strategy"
                strategy_folder = None
            
            if strategy_folder:
                # Clean up strategy name by removing LAJ_score_{model}_ prefix
                # Try case-insensitive match first
                pattern = re.compile(re.escape(f"LAJ_score_{model}_"), re.IGNORECASE)
                strategy = pattern.sub("", strategy_folder)
                
                # If that didn't work, try removing just "LAJ_score_" prefix
                if strategy == strategy_folder:
                    strategy = strategy_folder.replace("LAJ_score_", "")
                
                # If still contains model name at the start, remove it
                if strategy.lower().startswith(model.lower() + "_"):
                    strategy = strategy[len(model) + 1:]
            else:
                strategy = "Unknown_Strategy"

        # 4. PREPARE DATAFRAME
        df = pd.DataFrame(data)
        
        if 'human_score' not in df.columns or 'ai_score' not in df.columns:
            print(f"⚠️ [SKIP] {filename}: Missing columns")
            skipped_error_count += 1
            continue

        df = df.dropna(subset=['human_score', 'ai_score'])
        try:
            df['human_score'] = df['human_score'].astype(int)
            df['ai_score'] = df['ai_score'].astype(int)
        except:
             print(f"⚠️ [SKIP] {filename}: Non-numeric data")
             skipped_error_count += 1
             continue

        # 5. FILTER BINARY (0 or 1)
        valid_df = df[df['human_score'].isin([0, 1]) & df['ai_score'].isin([0, 1])]
        
        if valid_df.empty:
            print(f"⚠️ [SKIP] {filename}: No binary rows (0/1) found.")
            skipped_error_count += 1
            continue

        # 6. CALCULATE
        metrics = calculate_metrics(valid_df)
        if not metrics: 
            skipped_error_count += 1
            continue

        # 7. AGGREGATE
        if criterion not in aggregated_data: aggregated_data[criterion] = {}
        if model not in aggregated_data[criterion]: aggregated_data[criterion][model] = {}
        aggregated_data[criterion][model][strategy] = metrics
        count += 1
        print(f"✅ [OK] {filename}")

    # 8. SAVE RESULTS
    print("\n" + "="*40)
    print(f"✅ Processed Binary Files: {count}")
    print(f"🙈 Ignored Non-Target Files: {ignored_count} (IDs not in {TARGET_IDS})")
    print(f"❌ Errors/Empty:             {skipped_error_count}")
    print("="*40)

    for crit, models in aggregated_data.items():
        crit_dir = os.path.join(OUTPUT_DIR, crit)
        os.makedirs(crit_dir, exist_ok=True)
        
        for model, strategies in models.items():
            output_file = os.path.join(crit_dir, f"{model}.json")
            with open(output_file, 'w', encoding='utf-8') as out_f:
                json.dump(strategies, out_f, indent=4)
            print(f" 💾 Saved: {output_file}")

if __name__ == "__main__":
    main()