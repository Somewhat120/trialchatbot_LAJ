#!/usr/bin/env python3
"""
Normalize Field Names and Update Human Scores Script

This script:
1. Discovers field name variations for ai_score and human_score across files
2. Normalizes field names to standard ones (ai_score, human_score)
3. Updates human_score from Excel source
4. Generates reports on field name variations and updates

Works with the new directory structure (dataset=test_set0213/...).
Can process by criterion for easier manual handling.

Usage:
    # Discovery mode (dry-run)
    python score_postprocessing_scripts/03_normalize_and_update_scores.py \
        --json-dir LAJ_score_outputs_repaired/dataset=test_set0213 \
        --discover-only \
        --report field_names_report.xlsx

    # Normalize + Update human scores
    python score_postprocessing_scripts/03_normalize_and_update_scores.py \
        --json-dir LAJ_score_outputs_repaired/dataset=test_set0213 \
        --excel score_postprocessing_scripts/c5_checked.xlsx \
        --criterion c5_factual_accuracy \
        --output-dir LAJ_score_outputs_normalized/dataset=test_set0213

    # Normalize + Update human scores + update question/answer from a QA JSONL
    python score_postprocessing_scripts/03_normalize_and_update_scores.py \
        --json-dir LAJ_score_outputs_clean/dataset=test_set0213 \
        --qa-jsonl data_eval/test_set0226.jsonl \
        --output-dir LAJ_score_outputs_normalized/dataset=test_set0226 \
        --rewrite-prompt-method
"""

import json
import pandas as pd
import os
import sys
import argparse
import re
from pathlib import Path
from typing import Dict, Tuple, Optional, List, Any, Set
from collections import defaultdict

# Prompt name to Excel column mapping
PROMPT_TO_COLUMN = {
    "c1_critical_harm": "Presence of Critical Harms Risks",
    "c1_critical_harms": "Presence of Critical Harms Risks",
    "c2_individualized_medical_advice": "Presence of individualized medical advice",
    "c3_inappropriate_persuasion": "Presence of inappropriate persuasion",
    "c4_transparency": "Level of source transparency",
    "c5_factual_acc": "Level of factual accuracy",
    "c5_factual_accuracy": "Level of factual accuracy",
    "c6_usefulness": "Level of usefulness",
    "c7_clarity": "Level of Clarity and Succinctness",
    "c7_clarity_succinctness": "Level of Clarity and Succinctness",
    "c7_clarity_and_succinctness": "Level of Clarity and Succinctness",
    # Legacy mappings
    "1_critical_harm": "Presence of Critical Harms Risks",
    "2_individualized_advice": "Presence of individualized medical advice",
    "2_individualized_medical_advice": "Presence of individualized medical advice",
    "3_persuation": "Presence of inappropriate persuasion",
    "3_persuasion": "Presence of inappropriate persuasion",
    "4_transparent": "Level of source transparency",
    "4_transparency": "Level of source transparency",
    "5_factual_acc": "Level of factual accuracy",
    "6_useful": "Level of usefulness",
    "7_clarity": "Level of Clarity and Succinctness",
    "7_clarity_succinctness": "Level of Clarity and Succinctness",
    "7_clarity_and_succinctness": "Level of Clarity and Succinctness",
    "7_clarify": "Level of Clarity and Succinctness",
}

# Common field name variations to check
AI_SCORE_VARIATIONS = [
    "ai_score", "final_score", "score", "ai_judgment_score", 
    "judge_score", "judge_output", "ai_final_score"
]

HUMAN_SCORE_VARIATIONS = [
    "human_score", "human_label", "ground_truth", "gold_score"
] + list(PROMPT_TO_COLUMN.values())  # Add all criterion column names

PROMPT_COLUMNS_NORMALIZED = {c.lower(): c for c in PROMPT_TO_COLUMN.values()}

def extract_criterion_from_filename(filename: str) -> Optional[str]:
    """Extract criterion name from filename."""
    basename = os.path.splitext(filename)[0].lower()
    
    # Try cX_pattern first
    for key in PROMPT_TO_COLUMN.keys():
        if key.lower() in basename:
            return key
    
    # Try numeric pattern
    match = re.match(r'^c?(\d+)_([a-z_]+)', basename)
    if match:
        num, name = match.group(1), match.group(2).rstrip('_')
        candidate = f"c{num}_{name}"
        if candidate in PROMPT_TO_COLUMN:
            return candidate
        candidate = f"{num}_{name}"
        if candidate in PROMPT_TO_COLUMN:
            return candidate
    
    return None


def extract_path_segment(file_path: str, key: str) -> Optional[str]:
    """Extract a key=value segment from the file path (e.g. evidence=stage1_hybrid_rrf_...)."""
    key_lower = key.lower()
    parts = Path(file_path).parts
    for part in parts:
        if "=" in part:
            k, _, v = part.partition("=")
            if k.lower() == key_lower and v:
                return v
    return None


def find_score_fields(entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Find AI score and human score field names in an entry.
    Returns: (ai_score_field_name, human_score_field_name)
    """
    ai_score_field = None
    human_score_field = None
    
    # Check for AI score variations
    for field in AI_SCORE_VARIATIONS:
        if field in entry:
            value = entry[field]
            # Handle nested structures (e.g., judge_output.final_score)
            if isinstance(value, dict):
                if "final_score" in value:
                    ai_score_field = f"{field}.final_score"
                    break
                elif "score" in value:
                    ai_score_field = f"{field}.score"
                    break
            elif value is not None:
                ai_score_field = field
                break
    
    # Check for human score variations
    for field in HUMAN_SCORE_VARIATIONS:
        if field in entry:
            value = entry[field]
            if value is not None:
                human_score_field = field
                break
    
    return ai_score_field, human_score_field

def discover_field_names(directory: str, criterion_filter: Optional[str] = None, sample_size: int = 20) -> Dict[str, Any]:
    """
    Discover field name variations across files.
    Returns a dictionary with field name statistics.
    """
    jsonl_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".jsonl") and not file.startswith("extraction_todo"):
                if criterion_filter is None or criterion_filter.lower() in file.lower():
                    jsonl_files.append(os.path.join(root, file))
    
    field_stats = {
        "files_analyzed": 0,
        "ai_score_fields": defaultdict(int),  # {field_name: count}
        "human_score_fields": defaultdict(int),  # {field_name: count}
        "file_details": [],  # [{file_path, ai_field, human_field, criterion}]
        "missing_ai_score": [],
        "missing_human_score": []
    }
    
    for file_path in jsonl_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                entries = []
                for i, line in enumerate(f):
                    if i >= sample_size:
                        break
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            
            if not entries:
                continue
            
            # Analyze first entry (assume all entries in file have same structure)
            entry = entries[0]
            ai_field, human_field = find_score_fields(entry)
            
            criterion = extract_criterion_from_filename(os.path.basename(file_path))
            if not criterion:
                criterion = entry.get("criterion_id", "unknown")
            
            evidence_from_path = extract_path_segment(file_path, "evidence")
            evidence_tag = entry.get("evidence_tag")
            
            field_stats["files_analyzed"] += 1
            
            if ai_field:
                field_stats["ai_score_fields"][ai_field] += 1
            else:
                field_stats["missing_ai_score"].append(file_path)
            
            if human_field:
                field_stats["human_score_fields"][human_field] += 1
            else:
                field_stats["missing_human_score"].append(file_path)
            
            field_stats["file_details"].append({
                "file_path": file_path,
                "ai_score_field": ai_field,
                "human_score_field": human_field,
                "criterion": criterion,
                "evidence_from_path": evidence_from_path,
                "evidence_tag": evidence_tag,
            })
        except Exception as e:
            print(f"⚠️  Error analyzing {file_path}: {e}")
    
    return field_stats

def normalize_entry(entry: Dict[str, Any], ai_field: Optional[str], human_field: Optional[str]) -> Dict[str, Any]:
    """
    Normalize field names in an entry.
    Renames ai_score_field -> ai_score, human_score_field -> human_score
    """
    normalized = entry.copy()
    
    # Normalize AI score
    if ai_field and ai_field != "ai_score":
        if "." in ai_field:  # Nested field (e.g., judge_output.final_score)
            parts = ai_field.split(".")
            value = entry
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    value = None
                    break
            if value is not None:
                normalized["ai_score"] = value
                # Remove original nested field if it's a dict
                if isinstance(entry.get(parts[0]), dict):
                    normalized.pop(parts[0], None)
        else:
            if ai_field in normalized:
                normalized["ai_score"] = normalized.pop(ai_field)
    
    # Normalize human score
    if human_field and human_field != "human_score":
        if human_field in normalized:
            normalized["human_score"] = normalized.pop(human_field)
    
    return normalized

def load_human_scores_from_excel(excel_path: str, criterion_column_mapping: Dict[str, str]) -> Dict[str, Dict[int, int]]:
    """
    Load human scores from Excel file.
    Returns: {criterion_name: {qa_id: score}}
    """
    try:
        df = pd.read_excel(excel_path, engine='openpyxl')
    except Exception as e:
        raise ValueError(f"Error reading Excel file: {e}")
    
    if 'qa_id' not in df.columns:
        raise ValueError("Excel must have 'qa_id' column")
    
    all_mappings = {}
    
    for criterion_name, column_name in criterion_column_mapping.items():
        # Try exact match first
        if column_name not in df.columns:
            # Try case-insensitive match
            matching_cols = [col for col in df.columns if col.lower() == column_name.lower()]
            if not matching_cols:
                continue  # Skip if column doesn't exist
            column_name = matching_cols[0]  # Use the actual column name
        
        mapping_by_qa_id = {}
        
        for _, row in df.iterrows():
            qa_id = row.get('qa_id')
            human_score = row[column_name]
            
            if pd.isna(qa_id) or pd.isna(human_score):
                continue
            
            try:
                qa_id_int = int(float(qa_id))
                score_int = int(float(human_score))
                mapping_by_qa_id[qa_id_int] = score_int
            except (ValueError, TypeError):
                continue
        
        all_mappings[criterion_name] = mapping_by_qa_id
    
    return all_mappings

def load_qa_updates_from_jsonl(jsonl_path: str) -> Dict[int, Dict[str, Any]]:
    """
    Load QA updates from a JSONL file.
    Returns: {qa_id: {"question": str, "answer": str, "scores": {col_lower: value}}}
    """
    qa_updates: Dict[int, Dict[str, Any]] = {}
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            qa_id = row.get("qa_id")
            if qa_id is None:
                continue
            try:
                qa_id_int = int(float(qa_id))
            except (ValueError, TypeError):
                continue
            scores: Dict[str, Any] = {}
            for k, v in row.items():
                if not isinstance(k, str):
                    continue
                k_lower = k.lower()
                if k_lower in PROMPT_COLUMNS_NORMALIZED:
                    scores[k_lower] = v
            qa_updates[qa_id_int] = {
                "question": row.get("question"),
                "answer": row.get("answer"),
                "scores": scores,
            }
    return qa_updates

def rewrite_output_rel_path(rel_path: str, rewrite_prompt_method: bool) -> str:
    if not rewrite_prompt_method:
        return rel_path
    parts = Path(rel_path).parts
    rewritten = []
    for part in parts:
        if part == "method=prompt":
            rewritten.append("method=prompt_zeroshot")
        else:
            rewritten.append(part)
    return str(Path(*rewritten))

def process_single_file(
    file_path: str,
    output_path: Optional[str],
    human_score_mappings: Optional[Dict[str, Dict[int, int]]] = None,
    qa_updates: Optional[Dict[int, Dict[str, Any]]] = None,
    overwrite: bool = False,
    dry_run: bool = False
) -> Dict[str, Any]:
    """Process a single JSONL file: normalize fields and update human scores."""
    stats = {
        "total": 0,
        "normalized_ai": 0,
        "normalized_human": 0,
        "updated_human": 0,
        "updated_human_from_qa": 0,
        "skipped_human": 0,
        "missing_qa_id": 0,
        "missing_qa_in_updates": 0,
        "updated_question": 0,
        "updated_answer": 0,
        "evidence_from_path": None,
        "evidence_tag": None,
        "saved": False,
        "modified": False,
    }
    
    entries = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return stats
    
    stats["total"] = len(entries)
    stats["evidence_from_path"] = extract_path_segment(file_path, "evidence")
    stats["evidence_tag"] = entries[0].get("evidence_tag") if entries else None
    
    if not entries:
        return stats
    
    # Find field names from first entry
    ai_field, human_field = find_score_fields(entries[0])
    
    # Determine criterion
    criterion = extract_criterion_from_filename(os.path.basename(file_path))
    if not criterion:
        criterion = entries[0].get("criterion_id", "unknown")
        # Try to extract criterion from criterion_id if it's in format like "reliability_c5_factual_accuracy"
        if criterion != "unknown" and "_" in criterion:
            # Extract the criterion part (e.g., "c5_factual_accuracy" from "reliability_c5_factual_accuracy")
            parts = criterion.split("_", 1)
            if len(parts) > 1:
                potential_criterion = parts[1]  # e.g., "c5_factual_accuracy"
                # Check if this matches any key in PROMPT_TO_COLUMN
                for key in PROMPT_TO_COLUMN.keys():
                    if key.lower() == potential_criterion.lower() or key.lower() in potential_criterion.lower():
                        criterion = key
                        break
    
    # Get human score mapping for this criterion
    human_mapping = None
    if human_score_mappings:
        # Try exact match first
        if criterion in human_score_mappings:
            human_mapping = human_score_mappings[criterion]
        else:
            # Try to find matching criterion (handle variations)
            for key in human_score_mappings.keys():
                if key.lower() in criterion.lower() or criterion.lower() in key.lower():
                    human_mapping = human_score_mappings[key]
                    criterion = key  # Update criterion to the matched key
                    break
    
    normalized_entries = []
    modified = False
    needs_save = False  # Track if we need to save (even if no modifications)
    
    for entry in entries:
        # Normalize field names
        normalized = normalize_entry(entry, ai_field, human_field)
        
        if ai_field and ai_field != "ai_score":
            if ai_field in entry or "." in ai_field:
                stats["normalized_ai"] += 1
                modified = True
        
        if human_field and human_field != "human_score":
            if human_field in entry:
                stats["normalized_human"] += 1
                modified = True
        
        qa_id_int = None
        qa_id = normalized.get("qa_id")
        if qa_id is not None:
            try:
                qa_id_int = int(float(qa_id))
            except (ValueError, TypeError):
                qa_id_int = None

        # Update question/answer and human score from QA JSONL (authoritative)
        if qa_updates:
            if qa_id_int is not None and qa_id_int in qa_updates:
                qa_row = qa_updates[qa_id_int]
                new_q = qa_row.get("question")
                new_a = qa_row.get("answer")
                if new_q is not None and new_q != normalized.get("question"):
                    normalized["question"] = new_q
                    stats["updated_question"] += 1
                    modified = True
                if new_a is not None and new_a != normalized.get("answer"):
                    normalized["answer"] = new_a
                    stats["updated_answer"] += 1
                    modified = True

                if criterion in PROMPT_TO_COLUMN:
                    col_name = PROMPT_TO_COLUMN[criterion].lower()
                    if col_name in qa_row.get("scores", {}):
                        try:
                            qa_score = int(float(qa_row["scores"][col_name]))
                            normalized["human_score"] = qa_score
                            stats["updated_human_from_qa"] += 1
                            modified = True
                        except (ValueError, TypeError):
                            pass
            else:
                stats["missing_qa_in_updates"] += 1

        # Update human score from Excel (non-authoritative fallback)
        if human_mapping:
            if qa_id_int is not None:
                if qa_id_int in human_mapping:
                    excel_score = human_mapping[qa_id_int]
                    current_score = normalized.get("human_score")
                    if current_score is not None and not overwrite:
                        stats["skipped_human"] += 1
                        if current_score != excel_score:
                            needs_save = True
                    else:
                        normalized["human_score"] = excel_score
                        stats["updated_human"] += 1
                        modified = True
            else:
                stats["missing_qa_id"] += 1
        
        normalized_entries.append(normalized)

    stats["modified"] = modified

    # Save if modified OR if we need to save (e.g., for normalization or tracking)
    # If output_path is specified, always save to that location (copying/transforming files)
    # If output_path is None, only save if we made modifications (in-place update)
    should_save = (modified or needs_save) if output_path is None else True
    if should_save and not dry_run:
        output_file = output_path if output_path else file_path
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                for entry in normalized_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            stats["saved"] = True
        except Exception as e:
            print(f"❌ Error saving {output_file}: {e}")
            return stats
    
    return stats

def save_discovery_report(field_stats: Dict[str, Any], output_path: str):
    """Save field name discovery report to Excel."""
    try:
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Summary sheet
            summary_data = {
                "Metric": [
                    "Total files analyzed",
                    "Unique AI score field names",
                    "Unique human score field names",
                    "Files missing AI score",
                    "Files missing human score"
                ],
                "Value": [
                    field_stats["files_analyzed"],
                    len(field_stats["ai_score_fields"]),
                    len(field_stats["human_score_fields"]),
                    len(field_stats["missing_ai_score"]),
                    len(field_stats["missing_human_score"])
                ]
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)
            
            # AI score fields
            if field_stats["ai_score_fields"]:
                ai_df = pd.DataFrame([
                    {"Field Name": field, "File Count": count}
                    for field, count in sorted(field_stats["ai_score_fields"].items(), key=lambda x: -x[1])
                ])
                ai_df.to_excel(writer, sheet_name="AI Score Fields", index=False)
            
            # Human score fields
            if field_stats["human_score_fields"]:
                human_df = pd.DataFrame([
                    {"Field Name": field, "File Count": count}
                    for field, count in sorted(field_stats["human_score_fields"].items(), key=lambda x: -x[1])
                ])
                human_df.to_excel(writer, sheet_name="Human Score Fields", index=False)
            
            # File details
            if field_stats["file_details"]:
                details_df = pd.DataFrame(field_stats["file_details"])
                details_df.to_excel(writer, sheet_name="File Details", index=False)
            
            # Missing fields
            if field_stats["missing_ai_score"] or field_stats["missing_human_score"]:
                path_to_evidence = {
                    d["file_path"]: (d.get("evidence_from_path"), d.get("evidence_tag"))
                    for d in field_stats["file_details"]
                }
                missing_data = []
                for path in field_stats["missing_ai_score"]:
                    ev_path, ev_tag = path_to_evidence.get(path, (None, None))
                    missing_data.append({
                        "File Path": path, "Missing Field": "ai_score",
                        "evidence_from_path": ev_path, "evidence_tag": ev_tag,
                    })
                for path in field_stats["missing_human_score"]:
                    ev_path, ev_tag = path_to_evidence.get(path, (None, None))
                    missing_data.append({
                        "File Path": path, "Missing Field": "human_score",
                        "evidence_from_path": ev_path, "evidence_tag": ev_tag,
                    })
                pd.DataFrame(missing_data).to_excel(writer, sheet_name="Missing Fields", index=False)
        
        print(f"💾 Discovery report saved to: {output_path}")
    except Exception as e:
        print(f"❌ Error saving report: {e}")

def process_directory(
    directory: str,
    output_dir: Optional[str],
    excel_path: Optional[str] = None,
    qa_jsonl: Optional[str] = None,
    criterion_filter: Optional[str] = None,
    overwrite: bool = False,
    dry_run: bool = False,
    rewrite_prompt_method: bool = False
):
    """Process all JSONL files in directory."""
    jsonl_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".jsonl") and not file.startswith("extraction_todo"):
                if criterion_filter is None or criterion_filter.lower() in file.lower():
                    jsonl_files.append(os.path.join(root, file))
    
    if not jsonl_files:
        print(f"⚠️  No JSONL files found in {directory}")
        return
    
    # Load human score mappings if Excel provided
    human_score_mappings = None
    if excel_path and os.path.exists(excel_path):
        print(f"📖 Loading human scores from Excel: {excel_path}")
        try:
            # Load all criteria mappings
            human_score_mappings = load_human_scores_from_excel(excel_path, PROMPT_TO_COLUMN)
            print(f"   ✅ Loaded mappings for {len(human_score_mappings)} criteria")
        except Exception as e:
            print(f"   ⚠️  Warning: Could not load Excel: {e}")
    
    print(f"\n{'='*80}")
    mode = "DRY RUN - " if dry_run else ""
    print(f"🔧 {mode}NORMALIZING FIELD NAMES AND UPDATING HUMAN SCORES")
    print(f"{'='*80}")
    print(f"📂 Found {len(jsonl_files)} file(s) to process")
    if criterion_filter:
        print(f"   Filtered to criterion: {criterion_filter}")
    if excel_path:
        print(f"   Human scores source: {excel_path}")
    if overwrite:
        print("⚠️  Overwrite mode: Will replace existing human_score values")
    print(f"{'='*80}\n")
    
    total_stats = {
        "total": 0,
        "normalized_ai": 0,
        "normalized_human": 0,
        "updated_human": 0,
        "updated_human_from_qa": 0,
        "skipped_human": 0,
        "missing_qa_id": 0,
        "missing_qa_in_updates": 0,
        "updated_question": 0,
        "updated_answer": 0,
        "files_with_changes": 0,
        "files_written": 0
    }
    
    qa_updates = None
    if qa_jsonl and os.path.exists(qa_jsonl):
        print(f"📖 Loading QA updates from JSONL: {qa_jsonl}")
        qa_updates = load_qa_updates_from_jsonl(qa_jsonl)
        print(f"   ✅ Loaded QA updates for {len(qa_updates)} qa_id(s)")

    print(f"{'FILENAME':<50} | {'EVIDENCE':<22} | {'TOTAL':<6} | {'NORM AI':<8} | {'NORM HUMAN':<10} | {'UPDATED':<8}")
    for file_path in jsonl_files:
        filename = os.path.basename(file_path)
        if len(filename) > 50:
            filename = filename[:47] + "..."
        
        # Determine output path
        if output_dir:
            rel_path = os.path.relpath(file_path, directory)
            rel_path = rewrite_output_rel_path(rel_path, rewrite_prompt_method)
            output_path = os.path.join(output_dir, rel_path)
        else:
            output_path = None
        
        stats = process_single_file(
            file_path,
            output_path,
            human_score_mappings,
            qa_updates,
            overwrite,
            dry_run,
        )
        
        # Accumulate stats
        for key in total_stats:
            if key in stats:
                total_stats[key] += stats[key]
        
        if stats.get("modified"):
            total_stats["files_with_changes"] += 1
        if stats.get("saved"):
            total_stats["files_written"] += 1
        
        # Print summary
        evidence_display = stats.get("evidence_from_path") or stats.get("evidence_tag") or "—"
        if len(evidence_display) > 22:
            evidence_display = evidence_display[:19] + "..."
        status_icon = "✅" if (stats["normalized_ai"] > 0 or stats["normalized_human"] > 0 or stats["updated_human"] > 0) else "ℹ️"
        print(f"{status_icon} {filename:<50} | {evidence_display:<22} | "
              f"Total: {stats['total']:<5} | "
              f"Norm AI: {stats['normalized_ai']:<5} | "
              f"Norm Human: {stats['normalized_human']:<5} | "
              f"Updated: {stats['updated_human']:<5}")
    
    # Final summary
    print(f"\n{'='*80}")
    print("📊 SUMMARY")
    print(f"{'='*80}")
    print(f"Files scanned:              {len(jsonl_files)}")
    if output_dir:
        print(f"Files written:              {total_stats['files_written']}")
    print(f"Total entries:              {total_stats['total']}")
    print(f"AI score fields normalized: {total_stats['normalized_ai']}")
    print(f"Human score fields normalized: {total_stats['normalized_human']}")
    print(f"Human scores updated:       {total_stats['updated_human']}")
    print(f"Human scores updated (QA):  {total_stats.get('updated_human_from_qa', 0)}")
    print(f"Human scores skipped:      {total_stats['skipped_human']}")
    print(f"Entries missing qa_id:     {total_stats['missing_qa_id']}")
    print(f"Entries missing QA update: {total_stats.get('missing_qa_in_updates', 0)}")
    print(f"Questions updated:         {total_stats.get('updated_question', 0)}")
    print(f"Answers updated:           {total_stats.get('updated_answer', 0)}")
    print(f"Files with changes:         {total_stats['files_with_changes']}")
    print(f"{'='*80}\n")
    
    if dry_run:
        print("💡 This was a dry run. Remove --dry-run to apply changes.\n")

def main():
    parser = argparse.ArgumentParser(
        description="Normalize field names and update human scores in JSONL files"
    )
    parser.add_argument("--json-dir", required=True, help="Directory containing JSONL files")
    parser.add_argument("--excel", help="Path to Excel file with human scores (columns: qa_id, criterion columns)")
    parser.add_argument("--qa-jsonl", help="Path to QA JSONL with corrected question/answer and human scores")
    parser.add_argument("--criterion", help="Process only specific criterion (e.g., c5_factual_accuracy)")
    parser.add_argument("--output-dir", help="Directory to save normalized files (if not provided, overwrites original)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing human_score values")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")
    parser.add_argument("--discover-only", action="store_true", help="Only discover field name variations, don't normalize")
    parser.add_argument("--report", help="Path to save discovery report (Excel format)")
    parser.add_argument("--sample-size", type=int, default=20, help="Number of entries to sample per file for discovery (default: 20)")
    parser.add_argument("--rewrite-prompt-method", action="store_true", help="Rewrite method=prompt to method=prompt_zeroshot in output paths")
    
    args = parser.parse_args()
    
    if args.discover_only:
        # Discovery mode
        print(f"\n{'='*80}")
        print("🔍 DISCOVERING FIELD NAME VARIATIONS")
        print(f"{'='*80}\n")
        
        field_stats = discover_field_names(args.json_dir, args.criterion, args.sample_size)
        
        print(f"📊 Discovery Results:")
        print(f"   Files analyzed: {field_stats['files_analyzed']}")
        print(f"\n   AI Score Fields Found:")
        for field, count in sorted(field_stats['ai_score_fields'].items(), key=lambda x: -x[1]):
            print(f"      {field}: {count} files")
        
        print(f"\n   Human Score Fields Found:")
        for field, count in sorted(field_stats['human_score_fields'].items(), key=lambda x: -x[1]):
            print(f"      {field}: {count} files")
        
        if field_stats['missing_ai_score']:
            print(f"\n   ⚠️  Files missing AI score: {len(field_stats['missing_ai_score'])}")
        if field_stats['missing_human_score']:
            print(f"\n   ⚠️  Files missing human score: {len(field_stats['missing_human_score'])}")
        
        if args.report:
            save_discovery_report(field_stats, args.report)
    else:
        # Normalize and update mode
        process_directory(
            args.json_dir,
            args.output_dir,
            args.excel,
            args.qa_jsonl,
            args.criterion,
            args.overwrite,
            args.dry_run,
            args.rewrite_prompt_method
        )

if __name__ == "__main__":
    main()
