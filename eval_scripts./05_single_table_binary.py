import json
import pandas as pd

# Load the JSON file
with open('prompt_comp_results/3_persuasion/qwen3_30b.json', 'r') as f:
    data = json.load(f)

# Convert to DataFrame
rows = []
for strategy, metrics in data.items():
    row = {'Strategy': strategy}
    # Flatten nested dicts
    for key, value in metrics.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                row[f"{key}_{sub_key}"] = sub_value
        else:
            row[key] = value
    rows.append(row)

df = pd.DataFrame(rows)

# Reorder columns
priority = ["Strategy", "n", "accuracy", "f1", "mcc", "specificity", "sensitivity_recall"]
other = [c for c in df.columns if c not in priority]
df = df[priority + sorted(other)]

# Display
print(df.to_string(index=False))

# Save to CSV
df.to_csv('eval_results/table_4_transparent_qwen3_30b.csv', index=False)