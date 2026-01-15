import os
import json
import pandas as pd
import glob

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
# Input folder containing the criterion subfolders (e.g., 1_critical_harm)
INPUT_ROOT = "prompt_comp_results_add_round1"

# Output folder for the tables
OUTPUT_DIR = "eval_results"

# ==========================================
# 🛠️ PROCESSING LOGIC
# ==========================================
def flatten_metrics(metrics_dict):
    """
    Flattens nested dictionaries (like confusion_matrix) into single keys.
    Example: confusion_matrix: {TN: 63} -> confusion_matrix_TN: 63
    """
    flat_data = {}
    for key, value in metrics_dict.items():
        if isinstance(value, dict):
            # Recurse for nested dicts (e.g., confusion_matrix)
            for sub_key, sub_value in value.items():
                flat_data[f"{key}_{sub_key}"] = sub_value
        else:
            flat_data[key] = value
    return flat_data

def process_criterion_folder(folder_path, criterion_name):
    """Reads all JSONs in a folder and compiles them into a DataFrame."""
    json_files = glob.glob(os.path.join(folder_path, "*.json"))
    
    if not json_files:
        return None

    all_rows = []
    
    for file_path in json_files:
        # Get Model Name from filename (e.g., "GPT5.json" -> "GPT5")
        model_name = os.path.splitext(os.path.basename(file_path))[0]
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Iterate through strategies inside the file
            # Key = Strategy (e.g., "def_cot_demo"), Value = Metrics Dict
            for strategy, metrics in data.items():
                
                # 1. Start with Identifiers
                row = {
                    "Model": model_name,
                    "Strategy": strategy,
                    "Configuration": f"{model_name}_{strategy}" # Unique Row Name
                }
                
                # 2. Add Flattened Metrics
                flat_metrics = flatten_metrics(metrics)
                row.update(flat_metrics)
                
                all_rows.append(row)
                
        except Exception as e:
            print(f"❌ Error reading {file_path}: {e}")

    if not all_rows:
        return None

    # Create DataFrame
    df = pd.DataFrame(all_rows)
    
    # Reorder columns: Identifiers first, then 'n', then metrics
    cols = list(df.columns)
    priority_cols = ["Configuration", "Model", "Strategy", "n", "accuracy", "f1", "mcc"]
    
    # Sort columns: Priority ones first, then alphabetical rest
    sorted_cols = [c for c in priority_cols if c in cols] + \
                  sorted([c for c in cols if c not in priority_cols])
    
    return df[sorted_cols]

# ==========================================
# 🚀 MAIN EXECUTION
# ==========================================
def main():
    print(f"🚀 Scanning root: {INPUT_ROOT}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Walk through the directory tree
    # We assume structure: prompt_comp_results_binary / CRITERION_NAME / MODEL.json
    
    processed_count = 0
    
    for root, dirs, files in os.walk(INPUT_ROOT):
        # Check if this folder has JSON files (it is a criterion folder)
        json_files = [f for f in files if f.endswith(".json")]
        
        if len(json_files) > 0:
            criterion_name = os.path.basename(root)
            
            # Skip if we are accidentally in the root folder itself
            if root == INPUT_ROOT: continue
            
            print(f"\n📂 Processing Criterion: {criterion_name}")
            
            df = process_criterion_folder(root, criterion_name)
            
            if df is not None:
                # Save to CSV
                output_filename = f"table_{criterion_name}.csv"
                output_path = os.path.join(OUTPUT_DIR, output_filename)
                
                df.to_csv(output_path, index=False)
                print(f"  ✅ Saved table with {len(df)} rows to: {output_path}")
                processed_count += 1
            else:
                print(f"  ⚠️ No valid data found in {criterion_name}")

    print(f"\n✨ COMPLETED. Created {processed_count} tables in '{OUTPUT_DIR}'")

if __name__ == "__main__":
    main()