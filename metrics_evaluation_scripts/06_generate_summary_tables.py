#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


def _extract_meta(metrics_path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in Path(metrics_path).parts:
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _get_rank_score(criterion_id: str, metric: Dict[str, Any]) -> float:
    if criterion_id.startswith("safety_c") or criterion_id == "composite_safety":
        return float(metric.get("sensitivity", metric.get("sensitivity_recall", 0.0)) or 0.0)
    return float(metric.get("spearman_rho", (metric.get("correlations") or {}).get("spearman", 0.0)) or 0.0)


def _flatten(metric: Dict[str, Any]) -> Dict[str, Any]:
    acc_obj = metric.get("accuracy")
    acc_dict = acc_obj if isinstance(acc_obj, dict) else {}
    rel_obj = metric.get("reliability")
    rel_dict = rel_obj if isinstance(rel_obj, dict) else {}
    err_obj = metric.get("error")
    err_dict = err_obj if isinstance(err_obj, dict) else {}
    corr_obj = metric.get("correlations")
    corr_dict = corr_obj if isinstance(corr_obj, dict) else {}
    return {
        "n": metric.get("n"),
        "analysis_unit": metric.get("analysis_unit"),
        "sensitivity": metric.get("sensitivity", metric.get("sensitivity_recall")),
        "sensitivity_ci_low": (metric.get("sensitivity_ci") or {}).get("low"),
        "sensitivity_ci_high": (metric.get("sensitivity_ci") or {}).get("high"),
        "f1": metric.get("f1"),
        "f1_ci_low": (metric.get("f1_ci") or {}).get("low"),
        "f1_ci_high": (metric.get("f1_ci") or {}).get("high"),
        "specificity": metric.get("specificity"),
        "precision": metric.get("precision"),
        "mcc": metric.get("mcc"),
        "gwet_ac1": metric.get("gwet_ac1", metric.get("ac1")),
        "cohen_kappa": metric.get("cohen_kappa", metric.get("kappa")),
        "percent_agreement": metric.get("percent_agreement", metric.get("accuracy")),
        "balanced_accuracy": metric.get("balanced_accuracy"),
        "spearman_rho": metric.get("spearman_rho", corr_dict.get("spearman")),
        "spearman_rho_ci_low": (metric.get("spearman_rho_ci") or {}).get("low"),
        "spearman_rho_ci_high": (metric.get("spearman_rho_ci") or {}).get("high"),
        "within_1_point": metric.get("within_1_point", acc_dict.get("within_1_point")),
        "within_1_point_ci_low": (metric.get("within_1_point_ci") or {}).get("low"),
        "within_1_point_ci_high": (metric.get("within_1_point_ci") or {}).get("high"),
        "exact_match": metric.get("exact_match", acc_dict.get("exact_match")),
        "weighted_kappa_standard": metric.get("weighted_kappa_standard", rel_dict.get("kappa_standard")),
        "weighted_kappa_linear": metric.get("weighted_kappa_linear", rel_dict.get("kappa_linear")),
        "weighted_kappa_quadratic": metric.get("weighted_kappa_quadratic", rel_dict.get("kappa_quadratic")),
        "mae": metric.get("mae", err_dict.get("mae")),
        "rmse": metric.get("rmse", err_dict.get("rmse")),
        "pearson_r": metric.get("pearson_r", corr_dict.get("pearson")),
    }


def _best_variation(criterion_id: str, metrics_by_variation: Dict[str, Dict[str, Any]]) -> Tuple[Optional[str], Optional[Dict[str, Any]], float]:
    best_v = None
    best_m = None
    best_s = float("-inf")
    for variation, metric in metrics_by_variation.items():
        score = _get_rank_score(criterion_id, metric)
        if score > best_s:
            best_s = score
            best_v = variation
            best_m = metric
    return best_v, best_m, best_s


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate best-variation summary tables from eval outputs")
    ap.add_argument("--input_root", required=True, help="LAJ_eval_outputs root")
    ap.add_argument("--dataset_id", required=True)
    ap.add_argument("--output_dir", default="eval_results")
    ap.add_argument("--format", choices=["csv", "excel", "both"], default="both")
    args = ap.parse_args()

    base = Path(args.input_root) / f"dataset={args.dataset_id}"
    metrics_files = sorted(base.glob("metric_set=*/criterion=*/judge_model=*/method=*/evidence=*/metrics.json"))
    if not metrics_files:
        print(f"No metrics files found under: {base}")
        return

    detail_rows: List[Dict[str, Any]] = []
    best_by_model_criterion: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for p in metrics_files:
        meta = _extract_meta(str(p))
        criterion_id = meta.get("criterion")
        model = meta.get("judge_model")
        method = meta.get("method")
        evidence = meta.get("evidence")
        if not all([criterion_id, model, method, evidence]):
            continue
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict) or not payload:
            continue
        bv, bm, score = _best_variation(criterion_id, payload)
        if bv is None or bm is None:
            continue
        flat = _flatten(bm)
        row = {
            "Model": model,
            "Method": method,
            "Criterion": criterion_id,
            "Evidence": evidence,
            "Best_Variation": bv,
            "Ranking_Score": score,
            **flat,
        }
        detail_rows.append(row)

        k = (model, criterion_id, evidence)
        prev = best_by_model_criterion.get(k)
        if prev is None or score > prev["Ranking_Score"]:
            best_by_model_criterion[k] = row

    detail_df = pd.DataFrame(detail_rows)
    refined_df = pd.DataFrame(best_by_model_criterion.values())
    if refined_df.empty:
        print("No rows for refined table.")
        return

    refined_df = refined_df.rename(columns={"Method": "Best_Method"})
    refined_df["Version"] = refined_df["Best_Variation"]

    primary_secondary_cols = [
        "Model",
        "Best_Method",
        "Criterion",
        "Evidence",
        "Version",
        "Ranking_Score",
        "n",
        "analysis_unit",
        # Binary primary/secondary
        "sensitivity",
        "sensitivity_ci_low",
        "sensitivity_ci_high",
        "f1",
        "f1_ci_low",
        "f1_ci_high",
        # Ordinal primary/secondary
        "spearman_rho",
        "spearman_rho_ci_low",
        "spearman_rho_ci_high",
        "within_1_point",
        "within_1_point_ci_low",
        "within_1_point_ci_high",
    ]
    primary_secondary_df = refined_df[[c for c in primary_secondary_cols if c in refined_df.columns]].copy()

    os.makedirs(args.output_dir, exist_ok=True)
    detail_csv = Path(args.output_dir) / f"detail_table_{args.dataset_id}.csv"
    refined_csv = Path(args.output_dir) / f"refined_table_{args.dataset_id}.csv"
    primary_secondary_csv = Path(args.output_dir) / f"primary_secondary_table_{args.dataset_id}.csv"
    detail_xlsx = Path(args.output_dir) / f"detail_table_{args.dataset_id}.xlsx"
    refined_xlsx = Path(args.output_dir) / f"refined_table_{args.dataset_id}.xlsx"
    primary_secondary_xlsx = Path(args.output_dir) / f"primary_secondary_table_{args.dataset_id}.xlsx"

    if args.format in ("csv", "both"):
        detail_df.to_csv(detail_csv, index=False)
        refined_df.to_csv(refined_csv, index=False)
        primary_secondary_df.to_csv(primary_secondary_csv, index=False)
    if args.format in ("excel", "both"):
        detail_df.to_excel(detail_xlsx, index=False)
        refined_df.to_excel(refined_xlsx, index=False)
        primary_secondary_df.to_excel(primary_secondary_xlsx, index=False)

    print(f"Detail rows: {len(detail_df)}")
    print(f"Refined rows: {len(refined_df)}")
    print(f"Primary/Secondary rows: {len(primary_secondary_df)}")


if __name__ == "__main__":
    main()
