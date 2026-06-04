#!/usr/bin/env python3
"""
Update AI Score Script with Deduplication and Threshold Checking

Extracts ai_score from ai_reasoning/ai_judgment text in JSONL files.
- Handles duplicates: keeps first occurrence with valid score, else second
- Treats -1 as parsing error (invalid)
- Checks threshold: minimum 640 valid scores after deduplication (valid = ai_score in range; status is ignored)
- Only processes files that meet threshold

Usage:
    python score_postprocessing_scripts/02_update_ai_score.py \
        LAJ_score_outputs/dataset=test_set0213 \
        --criterion c7_clarity \
        --output-dir LAJ_score_outputs_repaired/dataset=test_set0213 \
        --min-valid-threshold 640
"""

import json
import os
import re
import argparse
import sys
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from collections import defaultdict

def get_valid_range(file_path: str, criterion_id: Optional[str] = None) -> List[int]:
    """Determine valid score range from filename or criterion_id."""
    base_name = os.path.basename(file_path).lower()
    
    if criterion_id:
        criterion_lower = criterion_id.lower()
        if any(f"c{i}_" in criterion_lower for i in [1, 2, 3]):
            return [0, 1]
        if "c4_" in criterion_lower or "transparency" in criterion_lower:
            return [1, 5]
        if any(f"c{i}_" in criterion_lower for i in [6, 7]):
            return [1, 2, 3, 4, 5]
    
    if any(f"c{i}_" in base_name for i in [1, 2, 3]):
        return [0, 1]
    if "c4_" in base_name or "transparency" in base_name:
        return [1, 5]
    if any(f"c{i}_" in base_name for i in [6, 7]):
        return [1, 2, 3, 4, 5]
    
    # Default: Likert 1-5
    return [1, 2, 3, 4, 5]

def _path_segment(file_path: str, prefix: str) -> str:
    for part in Path(file_path).parts:
        if part.startswith(prefix):
            return part.split("=", 1)[1]
    return ""


def should_include_file(file_path: str) -> bool:
    """Apply dataset-specific file selection policy."""
    # No C5 k5 filtering here; keep all available files.
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


def is_valid_score(score: Optional[int], valid_range: List[int]) -> bool:
    """Check if score is valid (not None, not -1, and in valid range)."""
    if score is None:
        return False
    if score == -1:  # Parsing error marker
        return False
    return score in valid_range

def extract_score_from_judgment(ai_judgment: str) -> Optional[int]:
    """Extract score from ai_judgment text."""
    if not ai_judgment:
        return None
    
    # Pattern 1: "Final score (0 or 1): 0" or "Final score (1 to 5): 5"
    pattern1 = re.search(r'Final score\s*\([^)]+\)\s*:\s*(\d+)', ai_judgment, re.IGNORECASE)
    if pattern1:
        return int(pattern1.group(1))
    
    # Pattern 2: "Final score: 5" (without parentheses)
    pattern2 = re.search(r'Final score\s*:\s*(\d+)', ai_judgment, re.IGNORECASE)
    if pattern2:
        return int(pattern2.group(1))
    
    # Pattern 3: JSON format with "final_score" key
    pattern3 = re.search(r'"final_score"\s*:\s*(\d+)', ai_judgment, re.IGNORECASE)
    if pattern3:
        return int(pattern3.group(1))
    
    # Pattern 4: Look for "score: X" or "score is X" near the end
    pattern4 = re.search(r'score\s*(?:is|:)\s*(\d+)', ai_judgment[-200:], re.IGNORECASE)
    if pattern4:
        return int(pattern4.group(1))
    
    return None

def resolve_duplicates(entries: List[Dict], valid_range: List[int]) -> Tuple[List[Dict], Dict]:
    """
    Resolve duplicate qa_ids: keep first with valid score, else second.
    
    Returns:
        (deduplicated_entries, duplicate_info)
    """
    # Group entries by qa_id
    qa_id_to_entries = defaultdict(list)  # {qa_id: [(index, entry, has_valid_score, has_reasoning)]}
    
    for i, entry in enumerate(entries):
        qa_id = entry.get("qa_id")
        if qa_id is None:
            continue
        
        try:
            qa_id_int = int(float(qa_id))
        except (ValueError, TypeError):
            continue
        
        # Check if current entry has valid score
        current_score = entry.get("ai_score")
        has_valid_score = is_valid_score(current_score, valid_range)
        
        # Check if has reasoning
        has_reasoning = bool(entry.get("ai_reasoning") or entry.get("ai_judgment"))
        
        qa_id_to_entries[qa_id_int].append((i, entry, has_valid_score, has_reasoning))
    
    # Resolve duplicates
    keep_indices = set()
    duplicate_info = {
        "duplicate_qa_ids": [],
        "resolutions": {}  # {qa_id: (kept_index, reason)}
    }
    
    for qa_id, entries_list in qa_id_to_entries.items():
        if len(entries_list) > 1:
            duplicate_info["duplicate_qa_ids"].append(qa_id)
            
            # Strategy: Find best entry to keep
            # Priority: 1) Has valid score, 2) Has reasoning, 3) First occurrence
            best_idx = None
            best_reason = None
            
            # Try to find one with valid score
            for idx, entry, has_valid_score, has_reasoning in entries_list:
                if has_valid_score:
                    best_idx = idx
                    best_reason = "has_valid_score"
                    break
            
            # If no valid score, try one with reasoning
            if best_idx is None:
                for idx, entry, has_valid_score, has_reasoning in entries_list:
                    if has_reasoning:
                        best_idx = idx
                        best_reason = "has_reasoning"
                        break
            
            # Fallback: first occurrence
            if best_idx is None:
                best_idx = entries_list[0][0]
                best_reason = "first_occurrence"
            
            keep_indices.add(best_idx)
            duplicate_info["resolutions"][qa_id] = (best_idx, best_reason)
        else:
            # No duplicate, keep it
            keep_indices.add(entries_list[0][0])
    
    # Build deduplicated list
    deduplicated = [entries[i] for i in sorted(keep_indices)]
    
    return deduplicated, duplicate_info

def process_single_file(
    file_path: str,
    overwrite: bool = False,
    dry_run: bool = False,
    output_path: Optional[str] = None,
    min_valid_threshold: int = 640,
    manual_edits: Optional[Dict[int, int]] = None,
    keep_invalid: bool = False
) -> dict:
    """Process a single JSONL file with deduplication and threshold checking."""
    if manual_edits is None:
        manual_edits = {}
    
    stats = {
        "total": 0,
        "after_dedup": 0,
        "updated": 0,
        "updated_manual": 0,
        "skipped": 0,
        "failed": 0,
        "already_exists": 0,
        "duplicates_removed": 0,
        "valid_scores_after_dedup": 0,
        "entries_saved": 0,
        "entries_filtered_out": 0,
        "meets_threshold": False,
        "threshold": min_valid_threshold
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
    
    # Get valid range for this file
    criterion_id = entries[0].get("criterion_id") if entries else None
    valid_range = get_valid_range(file_path, criterion_id)
    
    # Step 1: Resolve duplicates BEFORE processing
    deduplicated_entries, duplicate_info = resolve_duplicates(entries, valid_range)
    stats["after_dedup"] = len(deduplicated_entries)
    stats["duplicates_removed"] = stats["total"] - stats["after_dedup"]
    
    if duplicate_info["duplicate_qa_ids"]:
        print(f"   🔄 Found {len(duplicate_info['duplicate_qa_ids'])} duplicate qa_ids, resolved")
    
    # Step 2: Process entries (extract scores)
    modified = False
    
    for i, entry in enumerate(deduplicated_entries):
        qa_id = entry.get("qa_id")
        qa_id_int = None
        if qa_id is not None:
            try:
                qa_id_int = int(float(qa_id))
            except (ValueError, TypeError):
                pass
        
        current_score = entry.get("ai_score")
        
        # Check if already has valid score
        if is_valid_score(current_score, valid_range) and not overwrite:
            stats["already_exists"] += 1
            continue
        
        # Check for manual edits first (takes precedence)
        if qa_id_int is not None and qa_id_int in manual_edits:
            if not dry_run:
                entry["ai_score"] = manual_edits[qa_id_int]
            stats["updated_manual"] += 1
            modified = True
            continue
        
        # Treat -1 as invalid (needs extraction)
        if current_score == -1:
            # Clear it so we can extract
            if not dry_run:
                entry["ai_score"] = None
        
        # Get ai_reasoning or ai_judgment
        ai_judgment = entry.get("ai_reasoning") or entry.get("ai_judgment")
        
        if not ai_judgment:
            stats["skipped"] += 1
            continue
        
        # Extract score
        extracted_score = extract_score_from_judgment(ai_judgment)
        
        if extracted_score is None:
            stats["failed"] += 1
            if i < 5:  # Show first few failures for debugging
                print(f"   ⚠️  Row {i+1}: Could not extract score from judgment")
            continue
        
        # Update ai_score
        if not dry_run:
            entry["ai_score"] = extracted_score
        stats["updated"] += 1
        modified = True
    
    # Step 3: Count valid scores after deduplication and updates
    # Count entries with ai_score in valid range (status is not used for threshold)
    valid_count = 0
    for entry in deduplicated_entries:
        score = entry.get("ai_score")
        if is_valid_score(score, valid_range):
            valid_count += 1
    
    stats["valid_scores_after_dedup"] = valid_count
    stats["meets_threshold"] = valid_count >= min_valid_threshold
    
    # Step 4: Only save if meets threshold
    if not stats["meets_threshold"]:
        print(f"   ⚠️  Threshold not met: {valid_count} valid scores < {min_valid_threshold} required")
        if not dry_run:
            print(f"   ⏭️  Skipping file (threshold not met)")
            return stats
    
    # Save if meets threshold
    # - If using output_dir: always save files that meet threshold (to create cleaned versions)
    # - If overwriting original: only save if something changed (to avoid unnecessary writes)
    should_save = False
    if output_path:
        # If using output_dir, save all files that meet threshold (to create cleaned versions)
        should_save = stats["meets_threshold"]
    else:
        # If overwriting original, only save if something changed
        should_save = (modified or stats["duplicates_removed"] > 0) and stats["meets_threshold"]
    
    if should_save and not dry_run:
        output_file = output_path if output_path else file_path
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        try:
            # Filter to only valid scores unless keep_invalid is True
            if keep_invalid:
                entries_to_save = deduplicated_entries
            else:
                entries_to_save = [
                    entry for entry in deduplicated_entries
                    if is_valid_score(entry.get("ai_score"), valid_range)
                ]
                stats["entries_filtered_out"] = len(deduplicated_entries) - len(entries_to_save)
            
            stats["entries_saved"] = len(entries_to_save)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                for entry in entries_to_save:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"❌ Error saving {output_file}: {e}")
            return stats
    
    return stats

def load_manual_edits_from_excel(excel_path: str) -> Dict[int, int]:
    """Load manual ai_score edits from Excel file.
    
    Expected format:
    - Column: qa_id (required)
    - Column: ai_score (required)
    """
    try:
        import pandas as pd
    except ImportError:
        print("❌ pandas required for Excel support. Install with: pip install pandas openpyxl")
        return {}
    
    try:
        df = pd.read_excel(excel_path, engine='openpyxl')
    except Exception as e:
        print(f"❌ Error reading Excel file: {e}")
        return {}
    
    if 'qa_id' not in df.columns or 'ai_score' not in df.columns:
        print(f"⚠️  Excel must have 'qa_id' and 'ai_score' columns. Found: {list(df.columns)}")
        return {}
    
    manual_edits = {}
    for _, row in df.iterrows():
        qa_id = row.get('qa_id')
        ai_score = row.get('ai_score')
        
        if pd.isna(qa_id) or pd.isna(ai_score):
            continue
        
        try:
            qa_id_int = int(float(qa_id))
            score_int = int(float(ai_score))
            manual_edits[qa_id_int] = score_int
        except (ValueError, TypeError):
            continue
    
    return manual_edits

def process_directory(
    directory: str,
    criterion_filter: Optional[str] = None,
    overwrite: bool = False,
    dry_run: bool = False,
    output_dir: Optional[str] = None,
    min_valid_threshold: int = 640,
    manual_edits: Optional[Dict[int, int]] = None,
    keep_invalid: bool = False,
    exclude_log_excel: Optional[str] = None
):
    """Process all JSONL files in directory with threshold checking."""
    if manual_edits is None:
        manual_edits = {}
    
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
                if criterion_filter is None or criterion_filter.lower() in file.lower():
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
    print(f"🔧 {mode}EXTRACTING AI_SCORE FROM AI_REASONING")
    print(f"{'='*80}")
    print(f"📂 Found {len(jsonl_files)} file(s) to process")
    print(f"📊 Minimum threshold: {min_valid_threshold} valid scores after deduplication")
    if manual_edits:
        print(f"📝 Loaded {len(manual_edits)} manual score edits")
    if criterion_filter:
        print(f"   Filtered to criterion: {criterion_filter}")
    if overwrite:
        print("⚠️  Overwrite mode: Will replace existing ai_score values")
    print(f"{'='*80}\n")
    
    total_stats = {
        "total": 0,
        "after_dedup": 0,
        "updated": 0,
        "updated_manual": 0,
        "skipped": 0,
        "failed": 0,
        "already_exists": 0,
        "duplicates_removed": 0,
        "valid_scores_after_dedup": 0,
        "entries_saved": 0,
        "entries_filtered_out": 0,
        "files_processed": 0,
        "files_skipped_threshold": 0,
        "skipped_files": []  # Track which files were skipped
    }
    
    for file_path in jsonl_files:
        filename = os.path.basename(file_path)
        if len(filename) > 50:
            filename = filename[:47] + "..."
        
        # Determine output path
        if output_dir:
            rel_path = os.path.relpath(file_path, directory)
            output_path = os.path.join(output_dir, rel_path)
        else:
            output_path = None
        
        stats = process_single_file(file_path, overwrite, dry_run, output_path, min_valid_threshold, manual_edits, keep_invalid)
        
        # Accumulate stats
        total_stats["total"] += stats["total"]
        total_stats["after_dedup"] += stats["after_dedup"]
        total_stats["updated"] += stats["updated"]
        total_stats["updated_manual"] += stats["updated_manual"]
        total_stats["skipped"] += stats["skipped"]
        total_stats["failed"] += stats["failed"]
        total_stats["already_exists"] += stats["already_exists"]
        total_stats["duplicates_removed"] += stats["duplicates_removed"]
        total_stats["valid_scores_after_dedup"] += stats["valid_scores_after_dedup"]
        total_stats["entries_saved"] += stats.get("entries_saved", 0)
        total_stats["entries_filtered_out"] += stats.get("entries_filtered_out", 0)
        
        if stats["meets_threshold"]:
            total_stats["files_processed"] += 1
        else:
            total_stats["files_skipped_threshold"] += 1
            # Track skipped file with details
            total_stats["skipped_files"].append({
                "file_path": file_path,
                "rel_path": os.path.relpath(file_path, directory) if output_dir else file_path,
                "total": stats["total"],
                "after_dedup": stats["after_dedup"],
                "valid_scores": stats["valid_scores_after_dedup"],
                "threshold": min_valid_threshold
            })
            exclusions.append({
                "file_path": file_path,
                "reason": "threshold_not_met",
                "valid_scores_after_dedup": stats["valid_scores_after_dedup"],
                "threshold": min_valid_threshold,
            })
        
        # Print summary
        threshold_status = "✅" if stats["meets_threshold"] else "⚠️"
        status_icon = "✅" if stats["updated"] > 0 and stats["meets_threshold"] else "⚠️" if stats["already_exists"] > 0 else "❌"
        
        print(f"{status_icon} {filename:<50} | "
              f"Total: {stats['total']:<5} | "
              f"After Dedup: {stats['after_dedup']:<5} | "
              f"Valid: {stats['valid_scores_after_dedup']:<5} {threshold_status} | "
              f"Updated: {stats['updated']:<5} | "
              f"Failed: {stats['failed']:<5}")
    
    # Final summary
    print(f"\n{'='*80}")
    print("📊 SUMMARY")
    print(f"{'='*80}")
    print(f"Total entries:              {total_stats['total']}")
    print(f"After deduplication:         {total_stats['after_dedup']}")
    print(f"Duplicates removed:         {total_stats['duplicates_removed']}")
    print(f"✅ Valid scores (after dedup): {total_stats['valid_scores_after_dedup']}")
    print(f"✅ Successfully updated:     {total_stats['updated']}")
    if total_stats['updated_manual'] > 0:
        print(f"📝 Manual edits applied:     {total_stats['updated_manual']}")
    print(f"⏭️  Skipped (no judgment):   {total_stats['skipped']}")
    print(f"❌ Failed (no score found):  {total_stats['failed']}")
    print(f"ℹ️  Already had ai_score:    {total_stats['already_exists']}")
    if not keep_invalid and total_stats['entries_filtered_out'] > 0:
        print(f"🗑️  Entries filtered out:     {total_stats['entries_filtered_out']} (invalid scores removed)")
    print(f"💾 Entries saved:            {total_stats['entries_saved']}")
    print(f"\n📋 Files processed:          {total_stats['files_processed']}")
    print(f"⚠️  Files skipped (threshold): {total_stats['files_skipped_threshold']}")
    
    # Report skipped files
    if total_stats['skipped_files']:
        print(f"\n{'='*80}")
        print("⚠️  FILES SKIPPED (Below Threshold)")
        print(f"{'='*80}")
        print(f"{'FILE PATH':<70} | {'TOTAL':<8} | {'DEDUP':<8} | {'VALID':<8} | {'THRESHOLD':<10}")
        print("-" * 120)
        for skipped in total_stats['skipped_files']:
            rel_path = skipped['rel_path']
            if len(rel_path) > 68:
                rel_path = "..." + rel_path[-65:]
            print(f"{rel_path:<70} | "
                  f"{skipped['total']:<8} | "
                  f"{skipped['after_dedup']:<8} | "
                  f"{skipped['valid_scores']:<8} | "
                  f"{skipped['threshold']:<10}")
    
    print(f"{'='*80}\n")

    report_dir = output_dir or directory
    write_exclusion_report(exclusions, report_dir, exclude_log_excel)
    
    if dry_run:
        print("💡 This was a dry run. Remove --dry-run to apply changes.\n")

def main():
    parser = argparse.ArgumentParser(
        description="Extract ai_score from ai_reasoning text in JSONL files with deduplication and threshold checking"
    )
    parser.add_argument("input_path", help="Path to a JSONL file OR a directory of JSONL files")
    parser.add_argument("--criterion", help="Process only specific criterion (e.g., c7_clarity)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing ai_score values")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")
    parser.add_argument("--output-dir", help="Directory to save updated files (if not provided, overwrites original)")
    parser.add_argument("--min-valid-threshold", type=int, default=640, help="Minimum valid scores required after deduplication (default: 640)")
    parser.add_argument("--manual-edit-excel", help="Path to Excel file with manual ai_score edits (columns: qa_id, ai_score)")
    parser.add_argument("--keep-invalid", action="store_true", help="Keep entries without valid scores in output (default: filter them out)")
    parser.add_argument("--exclude-log-excel", help="Write excluded/filtered files report to this Excel path")
    
    args = parser.parse_args()
    
    # Load manual edits if provided
    manual_edits = {}
    if args.manual_edit_excel:
        print(f"📖 Loading manual edits from Excel: {args.manual_edit_excel}")
        manual_edits = load_manual_edits_from_excel(args.manual_edit_excel)
        if manual_edits:
            print(f"   ✅ Loaded {len(manual_edits)} manual edits")
        else:
            print(f"   ⚠️  No manual edits loaded")
    
    if os.path.isdir(args.input_path):
        process_directory(
            args.input_path,
            args.criterion,
            args.overwrite,
            args.dry_run,
            args.output_dir,
            args.min_valid_threshold,
            manual_edits,
            args.keep_invalid,
            args.exclude_log_excel
        )
    else:
        # Single file
        output_path = None
        if args.output_dir:
            filename = os.path.basename(args.input_path)
            output_path = os.path.join(args.output_dir, filename)
        
        stats = process_single_file(
            args.input_path,
            args.overwrite,
            args.dry_run,
            output_path,
            args.min_valid_threshold,
            manual_edits,
            args.keep_invalid
        )
        
        print(f"\n📊 Results:")
        print(f"   Total: {stats['total']}")
        print(f"   After deduplication: {stats['after_dedup']}")
        print(f"   Valid scores: {stats['valid_scores_after_dedup']}")
        print(f"   Meets threshold ({stats['threshold']}): {stats['meets_threshold']}")
        print(f"   Updated: {stats['updated']}")
        if stats['updated_manual'] > 0:
            print(f"   Manual edits: {stats['updated_manual']}")
        print(f"   Skipped: {stats['skipped']}")
        print(f"   Failed: {stats['failed']}")
        print(f"   Already exists: {stats['already_exists']}")
        if not args.keep_invalid and stats.get('entries_filtered_out', 0) > 0:
            print(f"   Entries filtered out: {stats['entries_filtered_out']}")
        print(f"   Entries saved: {stats.get('entries_saved', 0)}")

if __name__ == "__main__":
    main()
