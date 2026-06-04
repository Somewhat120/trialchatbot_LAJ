#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from common_eval import (
    BINARY_CRITERIA,
    DEFAULT_BOOTSTRAP_REPS,
    DEFAULT_BOOTSTRAP_SEED,
    calculate_binary_metrics,
    discover_score_files,
    ensure_dir,
    load_records,
    md5_file,
    score_frame_from_records,
    strict_eval_config_from_path,
    write_json,
    write_jsonl,
)


def flatten_binary_row(base: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        **base,
        "n": metrics.get("n"),
        "analysis_unit": metrics.get("analysis_unit"),
        "sensitivity": metrics.get("sensitivity"),
        "sensitivity_ci_low": (metrics.get("sensitivity_ci") or {}).get("low"),
        "sensitivity_ci_high": (metrics.get("sensitivity_ci") or {}).get("high"),
        "f1": metrics.get("f1"),
        "f1_ci_low": (metrics.get("f1_ci") or {}).get("low"),
        "f1_ci_high": (metrics.get("f1_ci") or {}).get("high"),
        "specificity": metrics.get("specificity"),
        "precision": metrics.get("precision"),
        "mcc": metrics.get("mcc"),
        "gwet_ac1": metrics.get("gwet_ac1"),
        "cohen_kappa": metrics.get("cohen_kappa"),
        "percent_agreement": metrics.get("percent_agreement"),
        "balanced_accuracy": metrics.get("balanced_accuracy"),
    }
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="Strict binary evaluator (C1-C3)")
    ap.add_argument("--input_root", required=True, help="Input score root (clean outputs)")
    ap.add_argument("--dataset_id", required=True)
    ap.add_argument("--output_root", required=True)
    ap.add_argument("--metric_set", default="binary_v1")
    ap.add_argument("--target_criteria", nargs="*", default=None)
    ap.add_argument("--bootstrap_reps", type=int, default=DEFAULT_BOOTSTRAP_REPS)
    ap.add_argument("--bootstrap_seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    files = discover_score_files(args.input_root)
    if not files:
        print(f"No jsonl files found under: {args.input_root}")
        return

    aggregated: Dict[Tuple[str, str, str, str], Dict[str, Dict[str, Any]]] = defaultdict(dict)
    manifest: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    rows_by_key: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    processed = 0
    skipped = 0

    for fp in files:
        cfg = strict_eval_config_from_path(fp)
        if cfg is None:
            skipped += 1
            continue
        if cfg.criterion_id not in BINARY_CRITERIA:
            skipped += 1
            continue
        if args.target_criteria and cfg.criterion_id not in args.target_criteria:
            continue

        recs = load_records(fp)
        df = score_frame_from_records(recs, cfg.criterion_id)
        if df.empty:
            skipped += 1
            continue

        metrics = calculate_binary_metrics(
            df,
            bootstrap_reps=args.bootstrap_reps,
            bootstrap_seed=args.bootstrap_seed,
        )
        key = (cfg.criterion_id, cfg.judge_model, cfg.method, cfg.evidence)
        aggregated[key][cfg.variation] = metrics
        manifest[key].append(
            {
                "source_file": fp,
                "source_md5": md5_file(fp),
                "variation": cfg.variation,
                "n_rows_used": int(metrics["n"]),
            }
        )
        rows_by_key[key].append(
            flatten_binary_row(
                {
                    "dataset": args.dataset_id,
                    "metric_set": args.metric_set,
                    "criterion_id": cfg.criterion_id,
                    "judge_model": cfg.judge_model,
                    "method": cfg.method,
                    "evidence": cfg.evidence,
                    "variation": cfg.variation,
                },
                metrics,
            )
        )
        processed += 1

    print(f"Processed files: {processed}")
    print(f"Skipped files:   {skipped}")

    for (criterion_id, model, method, evidence), variation_metrics in aggregated.items():
        out_dir = os.path.join(
            args.output_root,
            f"dataset={args.dataset_id}",
            f"metric_set={args.metric_set}",
            f"criterion={criterion_id}",
            f"judge_model={model}",
            f"method={method}",
            f"evidence={evidence}",
        )
        ensure_dir(out_dir)
        metrics_json = os.path.join(out_dir, "metrics.json")
        metrics_jsonl = os.path.join(out_dir, "metrics_by_variation.jsonl")
        manifest_json = os.path.join(out_dir, "run_manifest.json")
        if os.path.exists(metrics_json) and not args.force:
            continue
        write_json(metrics_json, variation_metrics)
        write_jsonl(metrics_jsonl, rows_by_key[(criterion_id, model, method, evidence)])
        write_json(
            manifest_json,
            {
                "dataset": args.dataset_id,
                "metric_set": args.metric_set,
                "criterion_id": criterion_id,
                "judge_model": model,
                "method": method,
                "evidence": evidence,
                "n_variations": len(variation_metrics),
                "sources": manifest[(criterion_id, model, method, evidence)],
                "strict_config_validation": True,
            },
        )


if __name__ == "__main__":
    main()
