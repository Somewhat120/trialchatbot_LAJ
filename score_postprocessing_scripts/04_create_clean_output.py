#!/usr/bin/env python3
"""
Create Clean/Preprocessed Output Script

This script filters JSONL files for evaluation:
- Filters to entries with both ai_score and human_score (valid scores)
- Keeps all other fields as-is
- Preserves directory structure

Usage:
    python score_postprocessing_scripts/04_create_clean_output.py \
        --json-dir LAJ_score_outputs_normalized/dataset=test_set0213 \
        --output-dir LAJ_score_outputs_clean/dataset=test_set0213 \
        --criterion c5_factual_accuracy
"""

import json
import os
import sys
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, Tuple
from pathlib import Path
from collections import defaultdict

# No field filtering - keep all fields, only filter by scores

def canonicalize_criterion(value: Optional[str]) -> str:
    """Map criterion ids/filenames/filter text to a canonical criterion key."""
    if not value:
        return "unknown"

    value_lower = value.lower()

    if "c1" in value_lower or "critical" in value_lower or "1_critical" in value_lower:
        return "c1_critical_harm"
    if "c2" in value_lower or "individualized" in value_lower or "2_individualized" in value_lower:
        return "c2_individualized_medical_advice"
    if "c3" in value_lower or "persuasion" in value_lower or "3_persuasion" in value_lower:
        return "c3_persuasion"
    if "c4" in value_lower or "transparency" in value_lower or "4_transparent" in value_lower:
        return "c4_transparency"
    if "c5" in value_lower or "factual" in value_lower or "5_factual" in value_lower:
        return "c5_factual_accuracy"
    if "c6" in value_lower or "usefulness" in value_lower or "6_useful" in value_lower:
        return "c6_usefulness"
    if "c7" in value_lower or "clarity" in value_lower or "7_clarity" in value_lower:
        return "c7_clarity"

    return "unknown"


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


def should_include_file(file_path: str) -> bool:
    """Apply dataset-specific file selection policy."""
    filename = os.path.basename(file_path).lower()
    method = extract_path_segment(file_path, "method") or ""
    method = method.lower()
    run_stamp = extract_path_segment(file_path, "run") or ""
    run_stamp = run_stamp.lower()
    evidence_name = extract_path_segment(file_path, "evidence") or ""
    evidence_name = evidence_name.lower()
    model_name = extract_path_segment(file_path, "model") or ""
    model_name = model_name.lower()

    return True


def should_skip_filename_variant(filename: str) -> bool:
    """Skip filename variants that are out of scope for primary scans."""
    filename_lower = filename.lower()
    return filename_lower.endswith("_v0.jsonl") or filename_lower.endswith("_v2.jsonl")


def write_exclusion_report(exclusions: list, output_dir: str, output_path: Optional[str]) -> None:
    if not exclusions:
        return
    try:
        import pandas as pd
    except Exception as e:
        print(f"⚠️  Could not write exclusion report (pandas missing): {e}")
        return
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(output_dir, f"excluded_files_{ts}.xlsx")
    try:
        pd.DataFrame(exclusions).to_excel(output_path, index=False)
        print(f"📄 Wrote exclusion report: {output_path}")
    except Exception as e:
        print(f"⚠️  Could not write exclusion report: {e}")


def is_valid_entry(entry: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Check if entry has both ai_score and human_score.
    Returns: (is_valid, missing_fields)
    """
    missing_fields = []
    
    # Check ai_score is valid (not None, not -1)
    ai_score = entry.get("ai_score")
    if ai_score is None or ai_score == -1:
        missing_fields.append("ai_score (missing or invalid)")
    
    # Check human_score exists (not None)
    if entry.get("human_score") is None:
        missing_fields.append("human_score (missing)")
    
    return len(missing_fields) == 0, missing_fields

def clean_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep entry as-is, only normalize chatbot_answer/answer field if needed.
    """
    # Normalize chatbot_answer/answer field if needed
    if "chatbot_answer" not in entry and "answer" in entry:
        entry = entry.copy()
        entry["chatbot_answer"] = entry.pop("answer")
    
    return entry

def process_single_file(
    file_path: str,
    output_path: str,
    dry_run: bool = False
) -> Dict[str, Any]:
    """Process a single JSONL file: filter and clean entries."""
    stats = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "missing_fields": defaultdict(int),
        "evidence_from_path": None,
        "evidence_tag": None,
    }
    
    entries = []
    valid_entries = []
    
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
    
    for entry in entries:
        is_valid, missing_fields = is_valid_entry(entry)
        
        if is_valid:
            cleaned = clean_entry(entry)
            valid_entries.append(cleaned)
            stats["valid"] += 1
        else:
            stats["invalid"] += 1
            for field in missing_fields:
                stats["missing_fields"][field] += 1
    
    # Save if not dry run
    if not dry_run and valid_entries:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                for entry in valid_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"❌ Error saving {output_path}: {e}")
            return stats
    
    return stats

def process_directory(
    directory: str,
    output_dir: str,
    criterion_filter: Optional[str] = None,
    dry_run: bool = False,
    exclude_log_excel: Optional[str] = None
):
    """Process all JSONL files in directory."""
    jsonl_files = []
    exclusions = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".jsonl") and not file.startswith("extraction_todo"):
                if should_skip_filename_variant(file):
                    exclusions.append({
                        "file_path": os.path.join(root, file),
                        "reason": "skip_variant_v0_v2",
                    })
                    continue
                full_path = os.path.join(root, file)
                if not should_include_file(full_path):
                    exclusions.append({
                        "file_path": full_path,
                        "reason": "should_include_file_false",
                    })
                    continue
                if criterion_filter is None:
                    jsonl_files.append(full_path)
                    continue

                filter_key = canonicalize_criterion(criterion_filter)
                file_key = canonicalize_criterion(file)
                if filter_key == "unknown":
                    if criterion_filter.lower() in file.lower():
                        jsonl_files.append(full_path)
                    else:
                        exclusions.append({
                            "file_path": full_path,
                            "reason": "criterion_filter_mismatch",
                            "criterion_filter": criterion_filter,
                        })
                elif file_key == filter_key:
                    jsonl_files.append(full_path)
                else:
                    exclusions.append({
                        "file_path": full_path,
                        "reason": "criterion_filter_mismatch",
                        "criterion_filter": criterion_filter,
                    })
    
    if not jsonl_files:
        print(f"⚠️  No JSONL files found in {directory}")
        return
    
    print(f"\n{'='*80}")
    mode = "DRY RUN - " if dry_run else ""
    print(f"🧹 {mode}CREATING CLEAN OUTPUT")
    print(f"{'='*80}")
    print(f"📂 Found {len(jsonl_files)} file(s) to process")
    if criterion_filter:
        print(f"   Filtered to criterion: {criterion_filter}")
    print(f"{'='*80}\n")
    
    total_stats = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "missing_fields": defaultdict(int),
        "files_processed": 0,
        "files_empty": 0
    }
    
    print(f"{'FILENAME':<50} | {'EVIDENCE':<22} | {'TOTAL':<6} | {'VALID':<6} | {'VALID %':<8} | {'INVALID':<6}")
    for file_path in jsonl_files:
        filename = os.path.basename(file_path)
        if len(filename) > 50:
            filename = filename[:47] + "..."
        
        # Determine output path
        rel_path = os.path.relpath(file_path, directory)
        output_path = os.path.join(output_dir, rel_path)
        
        stats = process_single_file(file_path, output_path, dry_run)
        
        # Accumulate stats
        total_stats["total"] += stats["total"]
        total_stats["valid"] += stats["valid"]
        total_stats["invalid"] += stats["invalid"]
        for field, count in stats["missing_fields"].items():
            total_stats["missing_fields"][field] += count
        
        if stats["valid"] > 0:
            total_stats["files_processed"] += 1
        else:
            total_stats["files_empty"] += 1
            exclusions.append({
                "file_path": file_path,
                "reason": "no_valid_entries",
                "total": stats["total"],
                "invalid": stats["invalid"],
            })
        
        # Print summary
        valid_pct = (stats["valid"] / stats["total"] * 100) if stats["total"] > 0 else 0
        evidence_display = stats.get("evidence_from_path") or stats.get("evidence_tag") or "—"
        if len(evidence_display) > 22:
            evidence_display = evidence_display[:19] + "..."
        status_icon = "✅" if stats["valid"] > 0 else "⚠️" if stats["invalid"] > 0 else "❌"
        
        print(f"{status_icon} {filename:<50} | {evidence_display:<22} | "
              f"{stats['total']:<6} | "
              f"{stats['valid']:<6} | "
              f"{valid_pct:.1f}%    | "
              f"{stats['invalid']:<6}")
    
    # Final summary
    print(f"\n{'='*80}")
    print("📊 SUMMARY")
    print(f"{'='*80}")
    print(f"Total entries:              {total_stats['total']}")
    print(f"✅ Valid entries:            {total_stats['valid']} ({total_stats['valid']/total_stats['total']*100:.1f}%)")
    print(f"❌ Invalid entries:          {total_stats['invalid']} ({total_stats['invalid']/total_stats['total']*100:.1f}%)")
    if total_stats['missing_fields']:
        print(f"\n   Missing fields breakdown:")
        for field, count in sorted(total_stats['missing_fields'].items(), key=lambda x: -x[1]):
            print(f"      {field}: {count}")
    print(f"\n📋 Files with valid entries: {total_stats['files_processed']}")
    print(f"⚠️  Files with no valid entries: {total_stats['files_empty']}")
    print(f"{'='*80}\n")
    
    if dry_run:
        print("💡 This was a dry run. Remove --dry-run to apply changes.\n")

    report_dir = output_dir or directory
    write_exclusion_report(exclusions, report_dir, exclude_log_excel)

def main():
    parser = argparse.ArgumentParser(
        description="Create clean/preprocessed output for evaluation"
    )
    parser.add_argument("--json-dir", required=True, help="Directory containing JSONL files")
    parser.add_argument("--output-dir", required=True, help="Directory to save cleaned files")
    parser.add_argument("--criterion", help="Process only specific criterion (e.g., c5_factual_accuracy)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be cleaned without making changes")
    parser.add_argument("--exclude-log-excel", help="Write excluded/filtered files report to this Excel path")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.json_dir):
        print(f"❌ Input directory does not exist: {args.json_dir}")
        sys.exit(1)
    
    process_directory(
        args.json_dir,
        args.output_dir,
        args.criterion,
        args.dry_run,
        args.exclude_log_excel
    )

if __name__ == "__main__":
    main()
