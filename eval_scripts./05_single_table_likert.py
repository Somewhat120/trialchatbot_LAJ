import json
import pandas as pd
import sys
import os
import argparse
import glob

def parse_strategy_key(key_string):
    """Extract strategy name from complex key."""
    # Check for demo variants first (they have _RAG_demo or _demo pattern)
    has_demo = "_demo" in key_string or key_string.endswith("demo")
    
    # For keys like "QWEN_def_only__QA__30b_def_only_NO_RAG_1224"
    # or "oracle_baseline__short_v1_baseline_RAG_demo_1224"
    if "def_cot" in key_string:
        return "def_cot_demo" if has_demo else "def_cot"
    elif "def_demo" in key_string:
        return "def_demo"
    elif "def_only" in key_string:
        return "def_only"
    elif "baseline" in key_string:
        return "baseline_demo" if has_demo else "baseline"
    else:
        return key_string  # Return original if no match

def flatten_metrics(metrics_dict):
    """Flatten nested Likert metrics into a flat dictionary."""
    flat_data = {}
    
    # Handle nested dictionaries
    if 'correlations' in metrics_dict:
        flat_data['pearson'] = metrics_dict['correlations'].get('pearson', None)
        flat_data['spearman'] = metrics_dict['correlations'].get('spearman', None)
    
    if 'accuracy' in metrics_dict:
        flat_data['exact_match'] = metrics_dict['accuracy'].get('exact_match', None)
        flat_data['within_1_point'] = metrics_dict['accuracy'].get('within_1_point', None)
    
    if 'reliability' in metrics_dict:
        flat_data['kappa_linear'] = metrics_dict['reliability'].get('kappa_linear', None)
        flat_data['kappa_quadratic'] = metrics_dict['reliability'].get('kappa_quadratic', None)
    
    if 'error' in metrics_dict:
        flat_data['mae'] = metrics_dict['error'].get('mae', None)
    
    # Add sample size
    if 'n' in metrics_dict:
        flat_data['n'] = metrics_dict['n']
    
    return flat_data

def json_to_table(json_file_path, output_csv_path=None):
    """Convert Likert JSON file to a table."""
    # Load JSON
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Error reading {json_file_path}: {e}")
        return None
    
    # Convert to list of rows
    rows = []
    for key_string, metrics in data.items():
        strategy = parse_strategy_key(key_string)
        
        row = {
            "Strategy": strategy,
            "Full_Key": key_string  # Keep original for reference
        }
        
        # Flatten metrics
        flat_metrics = flatten_metrics(metrics)
        row.update(flat_metrics)
        
        rows.append(row)
    
    if not rows:
        print(f"⚠️  No data found in {json_file_path}")
        return None
    
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Reorder columns: Strategy first, then n, then metrics
    priority_cols = [
        "Strategy", "n",
        "pearson", "spearman",
        "exact_match", "within_1_point",
        "kappa_linear", "kappa_quadratic",
        "mae"
    ]
    
    # Get columns that exist
    existing_priority = [c for c in priority_cols if c in df.columns]
    other_cols = [c for c in df.columns if c not in priority_cols]
    
    df = df[existing_priority + other_cols]
    
    # Print table
    print("\n" + "="*100)
    print(f"📊 Likert Scale Table from: {os.path.basename(json_file_path)}")
    print("="*100)
    print(df.to_string(index=False))
    print("="*100 + "\n")
    
    # Save to CSV if output path provided
    if output_csv_path:
        df.to_csv(output_csv_path, index=False)
        print(f"💾 Saved to: {output_csv_path}\n")
    elif len(sys.argv) > 2 and not os.path.isdir(sys.argv[1]):
        # Auto-generate output filename if not provided (only for single file mode)
        base_name = os.path.splitext(os.path.basename(json_file_path))[0]
        output_csv = f"table_{base_name}.csv"
        df.to_csv(output_csv, index=False)
        print(f"💾 Saved to: {output_csv}\n")
    
    return df

def process_directory(input_dir, output_dir=None):
    """Process all JSON files in a directory separately."""
    if not os.path.isdir(input_dir):
        print(f"❌ Error: {input_dir} is not a directory")
        return
    
    # Find all JSON files
    json_files = glob.glob(os.path.join(input_dir, "**", "*.json"), recursive=True)
    
    if not json_files:
        print(f"⚠️  No JSON files found in {input_dir}")
        return
    
    print(f"📂 Found {len(json_files)} JSON file(s) to process\n")
    
    # Create output directory if specified
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    processed = 0
    for json_file in json_files:
        # Generate output path
        if output_dir:
            base_name = os.path.splitext(os.path.basename(json_file))[0]
            # Preserve subdirectory structure
            rel_path = os.path.relpath(json_file, input_dir)
            rel_dir = os.path.dirname(rel_path)
            if rel_dir:
                output_subdir = os.path.join(output_dir, rel_dir)
                os.makedirs(output_subdir, exist_ok=True)
                output_csv = os.path.join(output_subdir, f"table_{base_name}.csv")
            else:
                output_csv = os.path.join(output_dir, f"table_{base_name}.csv")
        else:
            # Save in same directory as input file
            base_name = os.path.splitext(os.path.basename(json_file))[0]
            output_dir_local = os.path.dirname(json_file)
            output_csv = os.path.join(output_dir_local, f"table_{base_name}.csv")
        
        # Process file
        df = json_to_table(json_file, output_csv)
        if df is not None:
            processed += 1
    
    print(f"\n✨ COMPLETED. Processed {processed}/{len(json_files)} file(s)")

def main():
    parser = argparse.ArgumentParser(
        description="Convert Likert JSON evaluation results to readable tables"
    )
    parser.add_argument(
        "input_path",
        help="Path to a JSON file OR a directory of JSON files"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output CSV file path (for single file) or directory (for directory input)"
    )
    args = parser.parse_args()
    
    if not os.path.exists(args.input_path):
        print(f"❌ Error: Path not found: {args.input_path}")
        sys.exit(1)
    
    # Check if input is a directory or file
    if os.path.isdir(args.input_path):
        process_directory(args.input_path, args.output)
    else:
        # Single file mode
        json_to_table(args.input_path, args.output)

if __name__ == "__main__":
    main()