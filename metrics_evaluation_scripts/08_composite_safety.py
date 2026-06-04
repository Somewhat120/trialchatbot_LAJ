#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import pandas as pd

from common_eval import (
    DEFAULT_BOOTSTRAP_REPS,
    DEFAULT_BOOTSTRAP_SEED,
    calculate_binary_metrics,
    discover_score_files,
    ensure_dir,
    load_records,
    normalize_evidence_for_composite,
    parse_kv_from_path,
    score_frame_from_records,
    strict_eval_config_from_path,
    write_json,
    write_jsonl,
)


def _composite_slot_key(path: str) -> Tuple[str, str, str, str]:
    """Group C1/C2/C3 files by model, method, variation, and normalized evidence (no_rag_control → no_context)."""
    kv = parse_kv_from_path(path)
    raw_ev = kv.get("evidence", "")
    ev_out = normalize_evidence_for_composite(raw_ev)
    return (
        kv.get("model", ""),
        kv.get("method", ""),
        kv.get("variation", ""),
        ev_out,
    )


def _compose_for_slot(c1_path: str, c2_path: str, c3_path: str) -> pd.DataFrame:
    d1 = score_frame_from_records(load_records(c1_path), "safety_c1_critical_harm").rename(
        columns={"human_score": "h1", "ai_score": "a1"}
    )
    d2 = score_frame_from_records(load_records(c2_path), "safety_c2_individualized_medical_advice").rename(
        columns={"human_score": "h2", "ai_score": "a2"}
    )
    d3 = score_frame_from_records(load_records(c3_path), "safety_c3_inappropriate_persuasion").rename(
        columns={"human_score": "h3", "ai_score": "a3"}
    )

    merged = d1[["id", "h1", "a1"]].merge(d2[["id", "h2", "a2"]], on="id", how="inner").merge(
        d3[["id", "h3", "a3"]], on="id", how="inner"
    )
    if merged.empty:
        return pd.DataFrame(columns=["id", "human_score", "ai_score"])
    merged["human_score"] = ((merged["h1"] == 1) | (merged["h2"] == 1) | (merged["h3"] == 1)).astype(int)
    merged["ai_score"] = ((merged["a1"] == 1) | (merged["a2"] == 1) | (merged["a3"] == 1)).astype(int)
    return merged[["id", "human_score", "ai_score"]]


def _row_from_metric(dataset_id: str, metric_set: str, model: str, method: str, evidence: str, variation: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "dataset": dataset_id,
        "metric_set": metric_set,
        "criterion_id": "composite_safety",
        "judge_model": model,
        "method": method,
        "evidence": evidence,
        "variation": variation,
        "n": metrics.get("n"),
        "analysis_unit": "qa_pair",
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Composite safety (C1∪C2∪C3) strict per config slot")
    ap.add_argument("--input_root", required=True)
    ap.add_argument("--dataset_id", required=True)
    ap.add_argument("--output_root", required=True)
    ap.add_argument("--metric_set", default="composite_safety_v1")
    ap.add_argument("--bootstrap_reps", type=int, default=DEFAULT_BOOTSTRAP_REPS)
    ap.add_argument("--bootstrap_seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    by_slot: Dict[Tuple[str, str, str, str], Dict[str, str]] = defaultdict(dict)
    for fp in discover_score_files(args.input_root):
        cfg = strict_eval_config_from_path(fp)
        if cfg is None:
            continue
        if cfg.criterion_id not in {
            "safety_c1_critical_harm",
            "safety_c2_individualized_medical_advice",
            "safety_c3_inappropriate_persuasion",
        }:
            continue
        slot = _composite_slot_key(fp)
        prev_fp = by_slot[slot].get(cfg.criterion_id)
        if prev_fp is not None and prev_fp != fp:
            print(
                f"Warning: duplicate {cfg.criterion_id} for slot {slot}; keeping {fp!r} (was {prev_fp!r})",
                file=sys.stderr,
            )
        by_slot[slot][cfg.criterion_id] = fp

    all_rows: List[Dict[str, Any]] = []
    grouped_metrics: Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]] = defaultdict(dict)
    grouped_sources: Dict[Tuple[str, str, str], Dict[str, Dict[str, str]]] = defaultdict(dict)
    processed = 0
    for (model, method, variation, evidence_norm), paths in sorted(by_slot.items()):
        if not all(k in paths for k in (
            "safety_c1_critical_harm",
            "safety_c2_individualized_medical_advice",
            "safety_c3_inappropriate_persuasion",
        )):
            continue
        out_dir = os.path.join(
            args.output_root,
            f"dataset={args.dataset_id}",
            f"metric_set={args.metric_set}",
            "criterion=composite_safety",
            f"judge_model={model}",
            f"method={method}",
            f"evidence={evidence_norm}",
        )
        metrics_path = os.path.join(out_dir, "metrics.json")
        if os.path.exists(metrics_path) and not args.force:
            try:
                with open(metrics_path, "r", encoding="utf-8") as f:
                    existing_payload = json.load(f)
                if isinstance(existing_payload, dict) and variation in existing_payload:
                    continue
            except Exception:
                pass

        cdf = _compose_for_slot(
            paths["safety_c1_critical_harm"],
            paths["safety_c2_individualized_medical_advice"],
            paths["safety_c3_inappropriate_persuasion"],
        )
        if cdf.empty:
            continue
        metrics = calculate_binary_metrics(
            cdf,
            bootstrap_reps=args.bootstrap_reps,
            bootstrap_seed=args.bootstrap_seed,
        )
        slot_key = (model, method, evidence_norm)
        grouped_metrics[slot_key][variation] = metrics
        grouped_sources[slot_key][variation] = {
            "safety_c1_critical_harm": paths["safety_c1_critical_harm"],
            "safety_c2_individualized_medical_advice": paths["safety_c2_individualized_medical_advice"],
            "safety_c3_inappropriate_persuasion": paths["safety_c3_inappropriate_persuasion"],
        }
        processed += 1

    for (model, method, evidence_norm), variation_metrics in sorted(grouped_metrics.items()):
        out_dir = os.path.join(
            args.output_root,
            f"dataset={args.dataset_id}",
            f"metric_set={args.metric_set}",
            "criterion=composite_safety",
            f"judge_model={model}",
            f"method={method}",
            f"evidence={evidence_norm}",
        )
        ensure_dir(out_dir)
        metrics_path = os.path.join(out_dir, "metrics.json")
        metrics_jsonl = os.path.join(out_dir, "metrics_by_variation.jsonl")
        manifest_path = os.path.join(out_dir, "run_manifest.json")

        existing: Dict[str, Any] = {}
        if os.path.exists(metrics_path) and not args.force:
            try:
                with open(metrics_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, dict):
                    existing = payload
            except Exception:
                existing = {}
        merged = dict(existing)
        merged.update(variation_metrics)
        write_json(metrics_path, merged)

        slot_rows = [
            _row_from_metric(args.dataset_id, args.metric_set, model, method, evidence_norm, var, merged[var])
            for var in sorted(merged.keys())
        ]
        write_jsonl(metrics_jsonl, slot_rows)
        all_rows.extend(slot_rows)

        source_entries: List[Dict[str, str]] = []
        if os.path.exists(manifest_path) and not args.force:
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    existing_manifest = json.load(f)
                prev_sources = existing_manifest.get("sources")
                if isinstance(prev_sources, list):
                    source_entries.extend([s for s in prev_sources if isinstance(s, dict)])
            except Exception:
                pass
        for variation, src in sorted(grouped_sources[(model, method, evidence_norm)].items()):
            source_entries.extend(
                [
                    {
                        "variation": variation,
                        "criterion_id": "safety_c1_critical_harm",
                        "source_file": src["safety_c1_critical_harm"],
                    },
                    {
                        "variation": variation,
                        "criterion_id": "safety_c2_individualized_medical_advice",
                        "source_file": src["safety_c2_individualized_medical_advice"],
                    },
                    {
                        "variation": variation,
                        "criterion_id": "safety_c3_inappropriate_persuasion",
                        "source_file": src["safety_c3_inappropriate_persuasion"],
                    },
                ]
            )
        write_json(
            manifest_path,
            {
                "dataset": args.dataset_id,
                "metric_set": args.metric_set,
                "criterion_id": "composite_safety",
                "judge_model": model,
                "method": method,
                "evidence": evidence_norm,
                "n_variations": len(merged),
                "sources": source_entries,
                "strict_config_validation": True,
            },
        )

    summary_path = os.path.join(args.output_root, f"dataset={args.dataset_id}", "variation_ranking_summary.csv")
    if all_rows:
        pd.DataFrame(all_rows).sort_values(
            by=["method", "judge_model", "f1"], ascending=[True, True, False]
        ).to_csv(summary_path, index=False)
    print(f"Composite slots processed: {processed}")


if __name__ == "__main__":
    main()
