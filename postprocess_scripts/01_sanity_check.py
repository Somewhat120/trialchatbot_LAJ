#!/usr/bin/env python3
"""
Enhanced Sanity Check for LAJ Score Outputs

This script:
1. Checks required data fields for metrics calculation:
   - qa_id
   - criterion_id
   - human_score
   - ai_score
   - ai_reasoning (or ai_judgment, ai_reasoning_raw)
2. Validates score ranges based on criterion_id
3. Generates detailed missing data report
4. Groups results by criterion (C1..C7) derived from criterion_id

Per-record status in JSONL is only "ok" or "error". File-level status below
is a summary/error subtype for the whole file.

File-level status meanings:
   PASS                    - All entries have required fields and valid ai_score in range
   INCOMPLETE              - Fewer records than expected (vs --input_csv)
   FAIL (Duplicate qa_ids) - Same qa_id appears more than once in the file
   FAIL (Missing Fields)   - One or more required fields missing
   FAIL (Wrong Scale)      - ai_score present and outside valid range (not -1/null)
   PARTIAL (Needs Extraction) - ai_score missing or sentinel (-1); ai_reasoning present
   PARTIAL (Missing Scores)  - ai_score missing or sentinel (-1); no ai_reasoning
   ERROR                   - Could not read file or data is not a list

Usage:
    python postprocess_scripts/01_sanity_check.py \
        LAJ_score_outputs/dataset=test_set0213 \
        --input_csv data_eval/test_set0213.jsonl \
        --save-report sanity_check_report.json \
        --save-report-excel sanity_check_report.xlsx \
        --criterion c7_clarity
"""

import json
import os
import sys
import argparse
import pandas as pd
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from collections import defaultdict

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
# Sentinel value for "no score" (e.g. parse failure). Treated as missing, not wrong score.
AI_SCORE_MISSING_SENTINEL = -1

REQUIRED_FIELDS = {
    "qa_id": ["qa_id"],
    "criterion_id": ["criterion_id"],
    "human_score": ["human_score"],
    "ai_score": ["ai_score"],
    "ai_reasoning": ["ai_reasoning", "ai_judgment", "ai_reasoning_raw"],
}

def get_valid_range(filename: str, criterion_id: Optional[str] = None) -> List[int]:
    """Automatically determine scoring range from filename or criterion_id."""
    base_name = os.path.basename(filename).lower()
    
    # Check criterion_id first if available
    if criterion_id:
        criterion_lower = criterion_id.lower()
        if any(f"c{i}_" in criterion_lower for i in [1, 2, 3]):
            return [0, 1]
        if "c4_" in criterion_lower or "transparency" in criterion_lower:
            return [1, 5]
        if any(f"c{i}_" in criterion_lower for i in [6, 7]):
            return [1, 2, 3, 4, 5]
    
    # Binary scoring (0 or 1): c1, c2, c3
    if any(f"c{i}_" in base_name for i in [1, 2, 3]):
        return [0, 1]
    
    # Endpoints scoring (1 or 5): c4
    if "c4_" in base_name or "transparency" in base_name:
        return [1, 5]
    
    # Likert scoring (1-5): c6, c7
    if any(f"c{i}_" in base_name for i in [6, 7]):
        return [1, 2, 3, 4, 5]
    
    # Default to Likert if unknown
    return [1, 2, 3, 4, 5]

def check_field_presence(entry: Dict[str, Any], field_name: str, alternatives: List[str]) -> tuple:
    """Check if field exists (using alternatives) and return (found, used_field, value)."""
    for alt in alternatives:
        if alt in entry and entry[alt] is not None:
            value = entry[alt]
            # Check if value is not empty string
            if isinstance(value, str) and value.strip() == "":
                continue
            return True, alt, value
    return False, None, None

def extract_criterion_name(file_path: str, first_entry: Optional[Dict] = None) -> str:
    """Extract criterion name from file path or entry."""
    # Try from criterion_id in entry first
    if first_entry and first_entry.get("criterion_id"):
        criterion_id = first_entry.get("criterion_id").lower()
        if "c7" in criterion_id or "clarity" in criterion_id:
            return "c7_clarity"
        elif "c6" in criterion_id or "usefulness" in criterion_id:
            return "c6_usefulness"
        elif "c5" in criterion_id or "factual" in criterion_id:
            return "c5_factual_accuracy"
        elif "c4" in criterion_id or "transparency" in criterion_id:
            return "c4_transparency"
        elif "c3" in criterion_id or "persuasion" in criterion_id:
            return "c3_persuasion"
        elif "c2" in criterion_id or "individualized" in criterion_id:
            return "c2_individualized_medical_advice"
        elif "c1" in criterion_id or "critical" in criterion_id:
            return "c1_critical_harm"
    
    # Fallback: extract from filename
    filename = os.path.basename(file_path).lower()
    if "c1" in filename or "critical" in filename or "1_critical" in filename:
        return "c1_critical_harm"
    elif "c2" in filename or "individualized" in filename or "2_individualized" in filename:
        return "c2_individualized_medical_advice"
    elif "c3" in filename or "persuasion" in filename or "3_persuasion" in filename:
        return "c3_persuasion"
    elif "c4" in filename or "transparency" in filename or "4_transparent" in filename:
        return "c4_transparency"
    elif "c5" in filename or "factual" in filename or "5_factual" in filename:
        return "c5_factual_accuracy"
    elif "c6" in filename or "usefulness" in filename or "6_useful" in filename:
        return "c6_usefulness"
    elif "c7" in filename or "clarity" in filename or "7_clarity" in filename:
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


def analyze_file_enhanced(
    file_path: str, 
    expected_count: Optional[int] = None
) -> Dict[str, Any]:
    """Enhanced file analysis with comprehensive field checking and duplicate detection."""
    try:
        # Handle JSONL files
        if file_path.endswith('.jsonl'):
            data = []
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line:
                        try:
                            data.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            continue
        else:
            # Handle regular JSON files
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
    except Exception as e:
        return {"status": "ERROR", "msg": str(e), "file_path": file_path}

    if not isinstance(data, list):
        return {"status": "ERROR", "msg": "Not a list", "file_path": file_path}

    # Get criterion_id from first entry if available
    criterion_id = data[0].get("criterion_id") if data else None
    criterion_name = extract_criterion_name(file_path, data[0] if data else None)
    valid_range = get_valid_range(file_path, criterion_id)
    
    actual_count = len(data)
    
    stats = {
        "status": "PASS",
        "file_path": file_path,
        "criterion_id": criterion_id,
        "criterion_name": criterion_name,
        "evidence_from_path": extract_path_segment(file_path, "evidence"),
        "evidence_tag": data[0].get("evidence_tag") if data else None,
        "total": actual_count,
        "expected_count": expected_count,
        "valid_count": 0,
        "needs_extraction": [],
        "fatal_errors": 0,
        "scale_mismatch": 0,
        "valid_range": valid_range,
        "is_complete": True if expected_count is None else (actual_count == expected_count),
        "missing_fields": defaultdict(list),  # {field_name: [qa_id or record_id]}
        "wrong_scores": [],  # [(qa_id, ai_score, expected_range)]
        "missing_scores": [],  # [qa_id]
        "duplicate_qa_ids": [],  # [qa_id] that appear multiple times
        "duplicate_count": 0,
    }
    
    # Track qa_id occurrences for duplicate detection
    qa_id_counts = defaultdict(int)
    qa_id_to_rows = defaultdict(list)  # Track which row indices have each qa_id

    for i, entry in enumerate(data):
        # Get identifier
        qa_id = None
        qa_id_int = None
        has_qa_id, qa_id_field, qa_id_value = check_field_presence(entry, "qa_id", REQUIRED_FIELDS["qa_id"])
        if has_qa_id:
            qa_id = qa_id_value
            try:
                qa_id_int = int(float(qa_id))
                qa_id_counts[qa_id_int] += 1
                qa_id_to_rows[qa_id_int].append(i)
            except (ValueError, TypeError):
                pass
        else:
            qa_id = f"row_{i+1}"
        
        # Check all required fields
        entry_missing_fields = []
        entry_has_all_fields = True
        
        for field_name, alternatives in REQUIRED_FIELDS.items():
            if field_name == "qa_id":  # Already checked
                continue
            has_field, used_field, value = check_field_presence(entry, field_name, alternatives)
            if not has_field:
                entry_missing_fields.append(field_name)
                entry_has_all_fields = False
                stats["missing_fields"][field_name].append(qa_id)
        
        # Check ai_score validity. -1 and None are "missing" (not wrong score).
        ai_score_raw = entry.get("ai_score")
        is_score_valid = False
        score_val = None
        if ai_score_raw is not None:
            try:
                score_val = int(float(ai_score_raw))
            except (ValueError, TypeError):
                pass
        if score_val is not None and score_val != AI_SCORE_MISSING_SENTINEL:
            if score_val in valid_range:
                is_score_valid = True
            else:
                stats["scale_mismatch"] += 1
                stats["wrong_scores"].append((qa_id, score_val, valid_range))
        if not is_score_valid:
            has_reasoning, _, _ = check_field_presence(entry, "ai_reasoning", REQUIRED_FIELDS["ai_reasoning"])
            if has_reasoning:
                stats["needs_extraction"].append(qa_id)
            else:
                stats["missing_scores"].append(qa_id)
        
        # Determine entry status
        if entry_has_all_fields and is_score_valid:
            stats["valid_count"] += 1
        elif not entry_has_all_fields:
            stats["fatal_errors"] += 1
    
    # Detect duplicates
    duplicate_qa_ids = [qid for qid, count in qa_id_counts.items() if count > 1]
    stats["duplicate_qa_ids"] = duplicate_qa_ids
    stats["duplicate_count"] = len(duplicate_qa_ids)
    stats["qa_id_to_rows"] = {qid: rows for qid, rows in qa_id_to_rows.items() if qid in duplicate_qa_ids}

    # Determine final status (priority order)
    if expected_count is not None and actual_count < expected_count:
        stats["status"] = "INCOMPLETE"
    elif stats["duplicate_count"] > 0:
        stats["status"] = "FAIL (Duplicate qa_ids)"
    elif stats["fatal_errors"] > 0:
        stats["status"] = "FAIL (Missing Fields)"
    elif stats["scale_mismatch"] > 0:
        stats["status"] = "FAIL (Wrong Scale)"
    elif len(stats["needs_extraction"]) > 0:
        stats["status"] = "PARTIAL (Needs Extraction)"
    elif len(stats["missing_scores"]) > 0:
        stats["status"] = "PARTIAL (Missing Scores)"
    else:
        stats["status"] = "PASS"
    return stats

def get_expected_count_from_csv(csv_path: str) -> int:
    """Get expected count from input CSV/JSONL."""
    try:
        if csv_path.endswith('.jsonl'):
            count = 0
            with open(csv_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1
            return count
        else:
            df = pd.read_csv(csv_path, encoding='utf-8')
            return len(df)
    except Exception as e:
        return None

def process_directory_by_criterion(
    output_dir: str,
    input_csv: Optional[str] = None,
    criterion_filter: Optional[str] = None
) -> Dict[str, Any]:
    """Process directory, optionally by criterion."""
    if not os.path.exists(output_dir):
        return {
            "status": "ERROR",
            "msg": f"Output directory does not exist: {output_dir}",
        }
    
    # Get expected count from CSV if provided
    expected_count = None
    if input_csv and os.path.exists(input_csv):
        expected_count = get_expected_count_from_csv(input_csv)
        if expected_count is None:
            print(f"⚠️  Warning: Could not read expected count from CSV: {input_csv}")
    
    # Find all JSONL files
    jsonl_files = []
    for root, dirs, files in os.walk(output_dir):
        for file in files:
            if file.endswith(".jsonl") and not file.startswith("extraction_todo"):
                if criterion_filter is None or criterion_filter.lower() in file.lower():
                    jsonl_files.append(os.path.join(root, file))
    
    # Analyze files
    file_results = []
    grouped_by_criterion = defaultdict(list)
    
    for file_path in jsonl_files:
        result = analyze_file_enhanced(file_path, expected_count)
        file_results.append(result)
        
        # Group by criterion from first entry's criterion_id (e.g. safety_c1_critical_harm -> C1, 5_factual_acc_v0 -> C5)
        cid = (result.get("criterion_id") or "").lower()
        criterion = None
        for i in range(1, 8):
            if f"c{i}" in cid:
                criterion = f"C{i}"
                break
        if criterion is None:
            # Fallback for ids like 5_factual_acc_v0 (no "c" prefix)
            if "critical" in cid or "1_critical" in cid:
                criterion = "C1"
            elif "individualized" in cid or "2_individualized" in cid:
                criterion = "C2"
            elif "persuasion" in cid or "3_persuasion" in cid:
                criterion = "C3"
            elif "transparent" in cid or "4_transparent" in cid:
                criterion = "C4"
            elif "factual" in cid or "5_factual" in cid:
                criterion = "C5"
            elif "useful" in cid or "6_useful" in cid:
                criterion = "C6"
            elif "clarity" in cid or "7_clarity" in cid:
                criterion = "C7"
        if criterion:
            grouped_by_criterion[criterion].append(result)
        else:
            grouped_by_criterion["unknown"].append(result)
    
    # Generate missing data report
    missing_report = generate_missing_data_report(file_results, grouped_by_criterion)
    
    return {
        "status": "COMPLETE",
        "output_dir": output_dir,
        "file_results": file_results,
        "grouped_by_criterion": dict(grouped_by_criterion),
        "missing_report": missing_report,
        "total_files": len(jsonl_files),
    }

def generate_missing_data_report(
    file_results: List[Dict[str, Any]],
    grouped_by_criterion: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, Any]:
    """Generate comprehensive missing data report."""
    report = {
        "summary": {
            "total_files": len(file_results),
            "files_with_missing_fields": 0,
            "files_with_missing_scores": 0,
            "files_with_wrong_scores": 0,
            "files_with_duplicate_qa_ids": 0,
            "files_with_needs_extraction": 0,
            "total_missing_fields": defaultdict(int),
            "total_missing_scores": 0,
            "total_wrong_scores": 0,
            "total_duplicate_qa_ids": 0,
            "total_needs_extraction": 0,
        },
        "by_criterion": {},
        "by_file": [],
    }
    
    for criterion, results in grouped_by_criterion.items():
        criterion_report = {
            "files": len(results),
            "missing_fields": defaultdict(int),
            "missing_scores": [],
            "wrong_scores": [],
            "duplicate_qa_ids": [],
            "needs_extraction": [],
            "file_locations": [],
        }
        
        for result in results:
            if result.get("status") == "ERROR":
                continue
            
            criterion_id_val = result.get("criterion_id") or "unknown"
            
            # Aggregate missing fields
            for field_name, qa_ids in result.get("missing_fields", {}).items():
                criterion_report["missing_fields"][field_name] += len(qa_ids)
                report["summary"]["total_missing_fields"][field_name] += len(qa_ids)
            
            # Aggregate missing scores
            missing_scores = result.get("missing_scores", [])
            if missing_scores:
                criterion_report["missing_scores"].extend(missing_scores)
                report["summary"]["total_missing_scores"] += len(missing_scores)
            
            # Aggregate wrong scores
            wrong_scores = result.get("wrong_scores", [])
            if wrong_scores:
                criterion_report["wrong_scores"].extend(wrong_scores)
                report["summary"]["total_wrong_scores"] += len(wrong_scores)
            
            # Aggregate duplicates
            duplicate_qa_ids = result.get("duplicate_qa_ids", [])
            if duplicate_qa_ids:
                criterion_report["duplicate_qa_ids"].extend(duplicate_qa_ids)
                report["summary"]["total_duplicate_qa_ids"] += len(duplicate_qa_ids)
            
            # Aggregate needs_extraction (PARTIAL - ai_score missing/sentinel but ai_reasoning present)
            needs_extraction = result.get("needs_extraction", [])
            if needs_extraction:
                criterion_report["needs_extraction"].extend(needs_extraction)
                report["summary"]["total_needs_extraction"] += len(needs_extraction)
            
            # File location info (include files that only have needs_extraction)
            if (result.get("missing_fields") or result.get("missing_scores") or
                result.get("wrong_scores") or result.get("duplicate_qa_ids") or
                result.get("needs_extraction")):
                criterion_report["file_locations"].append({
                    "file_path": result["file_path"],
                    "criterion_id": criterion_id_val,
                    "evidence_from_path": result.get("evidence_from_path"),
                    "evidence_tag": result.get("evidence_tag"),
                    "missing_fields": dict(result.get("missing_fields", {})),
                    "missing_scores_count": len(result.get("missing_scores", [])),
                    "wrong_scores_count": len(result.get("wrong_scores", [])),
                    "duplicate_qa_ids": result.get("duplicate_qa_ids", []),
                    "duplicate_count": result.get("duplicate_count", 0),
                    "needs_extraction": result.get("needs_extraction", []),
                    "needs_extraction_count": len(result.get("needs_extraction", [])),
                })
        
        report["by_criterion"][criterion] = criterion_report
    
    # Update summary counts
    for result in file_results:
        if result.get("status") == "ERROR":
            continue
        if result.get("missing_fields"):
            report["summary"]["files_with_missing_fields"] += 1
        if result.get("missing_scores"):
            report["summary"]["files_with_missing_scores"] += 1
        if result.get("wrong_scores"):
            report["summary"]["files_with_wrong_scores"] += 1
        if result.get("duplicate_qa_ids"):
            report["summary"]["files_with_duplicate_qa_ids"] += 1
        if result.get("needs_extraction"):
            report["summary"]["files_with_needs_extraction"] += 1
    
    return report

def save_report_to_excel(missing_report: Dict[str, Any], output_path: str):
    """Save missing data report to Excel with multiple sheets."""
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # Summary sheet
        summary_data = []
        summary_data.append(["Total Files", missing_report["summary"]["total_files"]])
        summary_data.append(["Files with Missing Fields", missing_report["summary"]["files_with_missing_fields"]])
        summary_data.append(["Files with Missing Scores", missing_report["summary"]["files_with_missing_scores"]])
        summary_data.append(["Files with Wrong Scores", missing_report["summary"]["files_with_wrong_scores"]])
        summary_data.append(["Files with Duplicate qa_ids", missing_report["summary"]["files_with_duplicate_qa_ids"]])
        summary_data.append(["Files with Needs Extraction (PARTIAL)", missing_report["summary"]["files_with_needs_extraction"]])
        summary_data.append([])
        summary_data.append(["Total Missing Fields Breakdown", ""])
        for field_name, count in sorted(missing_report["summary"]["total_missing_fields"].items()):
            summary_data.append([field_name, count])
        summary_data.append([])
        summary_data.append(["Total Missing Scores", missing_report["summary"]["total_missing_scores"]])
        summary_data.append(["Total Wrong Scores", missing_report["summary"]["total_wrong_scores"]])
        summary_data.append(["Total Duplicate qa_ids", missing_report["summary"]["total_duplicate_qa_ids"]])
        summary_data.append(["Total Needs Extraction", missing_report["summary"]["total_needs_extraction"]])
        
        pd.DataFrame(summary_data, columns=["Metric", "Value"]).to_excel(
            writer, sheet_name="Summary", index=False
        )
        
        # Detailed issues sheet
        detail_rows = []
        for criterion, criterion_data in missing_report["by_criterion"].items():
            for file_loc in criterion_data["file_locations"]:
                file_path = file_loc["file_path"]
                criterion_id_val = file_loc.get("criterion_id", "unknown")
                
                # Missing fields
                evidence_from_path = file_loc.get("evidence_from_path")
                evidence_tag = file_loc.get("evidence_tag")
                for field_name, qa_ids in file_loc["missing_fields"].items():
                    for qa_id in qa_ids:
                        detail_rows.append({
                            "criterion": criterion,
                            "criterion_id": criterion_id_val,
                            "evidence_from_path": evidence_from_path,
                            "evidence_tag": evidence_tag,
                            "file_path": file_path,
                            "qa_id": qa_id,
                            "issue_type": "missing_field",
                            "field_name": field_name,
                            "details": f"Missing {field_name}",
                        })
                
                # Missing scores
                for qa_id in file_loc.get("missing_scores", []):
                    detail_rows.append({
                        "criterion": criterion,
                        "criterion_id": criterion_id_val,
                        "evidence_from_path": evidence_from_path,
                        "evidence_tag": evidence_tag,
                        "file_path": file_path,
                        "qa_id": qa_id,
                        "issue_type": "missing_score",
                        "field_name": "ai_score",
                        "details": "Missing ai_score and no ai_reasoning (or sentinel -1)",
                    })
                
                # Needs extraction (PARTIAL - ai_score missing/sentinel but ai_reasoning present)
                for qa_id in file_loc.get("needs_extraction", []):
                    detail_rows.append({
                        "criterion": criterion,
                        "criterion_id": criterion_id_val,
                        "evidence_from_path": evidence_from_path,
                        "evidence_tag": evidence_tag,
                        "file_path": file_path,
                        "qa_id": qa_id,
                        "issue_type": "needs_extraction",
                        "field_name": "ai_score",
                        "details": "ai_score missing or -1; ai_reasoning present (run 02_update_ai_score.py to extract)",
                    })
                
                # Wrong scores
                wrong_count = file_loc.get("wrong_scores_count", 0)
                if wrong_count > 0:
                    detail_rows.append({
                        "criterion": criterion,
                        "criterion_id": criterion_id_val,
                        "evidence_from_path": evidence_from_path,
                        "evidence_tag": evidence_tag,
                        "file_path": file_path,
                        "qa_id": "multiple",
                        "issue_type": "wrong_score",
                        "field_name": "ai_score",
                        "details": f"{wrong_count} entries with scores outside valid range",
                    })
                
                # Duplicate qa_ids
                for qa_id in file_loc.get("duplicate_qa_ids", []):
                    detail_rows.append({
                        "criterion": criterion,
                        "criterion_id": criterion_id_val,
                        "evidence_from_path": evidence_from_path,
                        "evidence_tag": evidence_tag,
                        "file_path": file_path,
                        "qa_id": qa_id,
                        "issue_type": "duplicate_qa_id",
                        "field_name": "qa_id",
                        "details": f"qa_id {qa_id} appears multiple times in this file",
                    })
        
        if detail_rows:
            pd.DataFrame(detail_rows).to_excel(
                writer, sheet_name="Detailed Issues", index=False
            )
        
        # By criterion summary
        criterion_summary = []
        for criterion, criterion_data in missing_report["by_criterion"].items():
            criterion_summary.append({
                "criterion": criterion,
                "files": criterion_data["files"],
                "missing_fields_total": sum(criterion_data["missing_fields"].values()),
                "missing_scores_total": len(criterion_data["missing_scores"]),
                "wrong_scores_total": len(criterion_data["wrong_scores"]),
                "duplicate_qa_ids_total": len(criterion_data["duplicate_qa_ids"]),
                "needs_extraction_total": len(criterion_data["needs_extraction"]),
                "affected_files": len(criterion_data["file_locations"]),
            })
        
        if criterion_summary:
            pd.DataFrame(criterion_summary).to_excel(
                writer, sheet_name="By Criterion", index=False
            )

def main():
    parser = argparse.ArgumentParser(
        description="Enhanced Sanity Check for LAJ Evaluation Outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("output_dir", help="Path to output directory to check")
    parser.add_argument("--input_csv", help="Path to input CSV/JSONL to determine expected counts")
    parser.add_argument("--criterion", help="Process only specific criterion (e.g., c7_clarity)")
    parser.add_argument("--save-report", help="Path to save detailed JSON report")
    parser.add_argument("--save-report-excel", help="Path to save missing data report as Excel")
    
    args = parser.parse_args()
    
    result = process_directory_by_criterion(
        args.output_dir,
        args.input_csv,
        args.criterion
    )
    
    if result.get("status") == "ERROR":
        print(f"❌ {result.get('msg')}")
        sys.exit(1)
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"📊 ENHANCED SANITY CHECK: {args.output_dir}")
    if args.criterion:
        print(f"   Filtered to criterion: {args.criterion}")
    print(f"{'='*80}\n")
    
    print(f"Total files analyzed: {result['total_files']}")
    
    # Print missing data report summary
    missing_report = result["missing_report"]
    summary = missing_report["summary"]
    
    print(f"\n📋 Missing Data Summary:")
    print(f"   Files with missing fields: {summary['files_with_missing_fields']}")
    print(f"   Files with missing scores: {summary['files_with_missing_scores']}")
    print(f"   Files with wrong scores: {summary['files_with_wrong_scores']}")
    print(f"   Files with needs extraction (PARTIAL): {summary['files_with_needs_extraction']}")
    
    if summary["total_missing_fields"]:
        print(f"\n   Missing fields breakdown:")
        for field_name, count in sorted(summary["total_missing_fields"].items()):
            print(f"      {field_name}: {count}")
    
    print(f"\n   Total missing scores: {summary['total_missing_scores']}")
    print(f"   Total wrong scores: {summary['total_wrong_scores']}")
    print(f"   Total duplicate qa_ids: {summary['total_duplicate_qa_ids']}")
    print(f"   Total needs extraction: {summary['total_needs_extraction']}")
    
    # Print by criterion
    if missing_report["by_criterion"]:
        print(f"\n📁 By Criterion:")
        for criterion, criterion_data in sorted(missing_report["by_criterion"].items()):
            print(f"   {criterion}:")
            print(f"      Files: {criterion_data['files']}")
            if criterion_data["missing_fields"]:
                print(f"      Missing fields: {dict(criterion_data['missing_fields'])}")
            if criterion_data["missing_scores"]:
                print(f"      Missing scores: {len(criterion_data['missing_scores'])} entries")
            if criterion_data["wrong_scores"]:
                print(f"      Wrong scores: {len(criterion_data['wrong_scores'])} entries")
            if criterion_data["duplicate_qa_ids"]:
                print(f"      Duplicate qa_ids: {len(criterion_data['duplicate_qa_ids'])} entries")
            if criterion_data["needs_extraction"]:
                print(f"      Needs extraction: {len(criterion_data['needs_extraction'])} entries")
            if criterion_data["file_locations"]:
                print(f"      Affected files: {len(criterion_data['file_locations'])}")
    
    # Print file-level summary
    print(f"\n{'='*80}")
    print(f"{'STATUS':<25} | {'EVIDENCE':<28} | {'FILE PATH':<42} | {'COUNT':<8} | {'VALID':<6} | {'TO-FIX':<6} | {'RANGE'}")
    print("-" * 140)
    
    for file_result in result["file_results"]:
        if file_result.get("status") == "ERROR":
            fpath = file_result["file_path"]
            rel_path = os.path.relpath(fpath, args.output_dir)
            if len(rel_path) > 40: rel_path = "..." + rel_path[-37:]
            print(f"❌ ERROR reading {rel_path}: {file_result.get('msg', 'Unknown error')}")
            continue
        
        fpath = file_result["file_path"]
        rel_path = os.path.relpath(fpath, args.output_dir)
        if len(rel_path) > 40: rel_path = "..." + rel_path[-37:]
        
        evidence_display = file_result.get("evidence_from_path") or file_result.get("evidence_tag") or "—"
        if len(evidence_display) > 27: evidence_display = evidence_display[:24] + "..."
        
        status_icon = "✅" if file_result["status"] == "PASS" else "⚠️" if "PARTIAL" in file_result["status"] or "INCOMPLETE" in file_result["status"] else "❌"
        range_str = str(file_result.get("valid_range", "?"))
        
        count_str = f"{file_result['total']}"
        if file_result.get("expected_count") is not None:
            if file_result['total'] < file_result['expected_count']:
                count_str = f"{file_result['total']}/{file_result['expected_count']} ⚠️"
            else:
                count_str = f"{file_result['total']}/{file_result['expected_count']}"
        
        to_fix = len(file_result.get("needs_extraction", [])) + len(file_result.get("missing_scores", []))
        
        print(f"{status_icon} {file_result['status']:<22} | {evidence_display:<28} | {rel_path:<42} | {count_str:<8} | "
              f"{file_result['valid_count']:<6} | {to_fix:<6} | {range_str}")
    
    # Save reports
    if args.save_report:
        with open(args.save_report, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n💾 Detailed report saved to: {args.save_report}")
    
    if args.save_report_excel:
        save_report_to_excel(missing_report, args.save_report_excel)
        print(f"💾 Missing data locations Excel saved to: {args.save_report_excel}")
    
    sys.exit(0 if result["status"] == "COMPLETE" else 1)

if __name__ == "__main__":
    main()

