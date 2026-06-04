#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


BINARY_COLUMNS = [
    "n",
    "analysis_unit",
    "sensitivity",
    "sensitivity_ci_low",
    "sensitivity_ci_high",
    "f1",
    "f1_ci_low",
    "f1_ci_high",
    "specificity",
    "precision",
    "mcc",
    "gwet_ac1",
    "cohen_kappa",
    "percent_agreement",
    "balanced_accuracy",
]

LIKERT_COLUMNS = [
    "n",
    "analysis_unit",
    "spearman_rho",
    "spearman_rho_ci_low",
    "spearman_rho_ci_high",
    "within_1_point",
    "within_1_point_ci_low",
    "within_1_point_ci_high",
    "exact_match",
    "weighted_kappa_standard",
    "weighted_kappa_linear",
    "weighted_kappa_quadratic",
    "mae",
    "rmse",
    "pearson_r",
]


def _extract_meta(metrics_path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in metrics_path.parts:
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _to_row(meta: Dict[str, str], variation: str, metric: Dict[str, Any], kind: str) -> Dict[str, Any]:
    row = {
        "Model": meta.get("judge_model"),
        "Method": meta.get("method"),
        "Criterion": meta.get("criterion"),
        "Evidence": meta.get("evidence"),
        "Variation": variation,
    }
    if kind == "binary":
        row.update(
            {
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
            }
        )
    else:
        row.update(
            {
                "n": metric.get("n"),
                "analysis_unit": metric.get("analysis_unit"),
                "spearman_rho": metric.get("spearman_rho", (metric.get("correlations") or {}).get("spearman")),
                "spearman_rho_ci_low": (metric.get("spearman_rho_ci") or {}).get("low"),
                "spearman_rho_ci_high": (metric.get("spearman_rho_ci") or {}).get("high"),
                "within_1_point": metric.get("within_1_point", (metric.get("accuracy") or {}).get("within_1_point")),
                "within_1_point_ci_low": (metric.get("within_1_point_ci") or {}).get("low"),
                "within_1_point_ci_high": (metric.get("within_1_point_ci") or {}).get("high"),
                "exact_match": metric.get("exact_match", (metric.get("accuracy") or {}).get("exact_match")),
                "weighted_kappa_standard": metric.get("weighted_kappa_standard", (metric.get("reliability") or {}).get("kappa_standard")),
                "weighted_kappa_linear": metric.get("weighted_kappa_linear", (metric.get("reliability") or {}).get("kappa_linear")),
                "weighted_kappa_quadratic": metric.get("weighted_kappa_quadratic", (metric.get("reliability") or {}).get("kappa_quadratic")),
                "mae": metric.get("mae", (metric.get("error") or {}).get("mae")),
                "rmse": metric.get("rmse", (metric.get("error") or {}).get("rmse")),
                "pearson_r": metric.get("pearson_r", (metric.get("correlations") or {}).get("pearson")),
            }
        )
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate exhaustive table with all required metrics + CI columns")
    ap.add_argument("--input_root", required=True, help="LAJ_eval_outputs root")
    ap.add_argument("--dataset_id", required=True)
    ap.add_argument("--output_dir", default="eval_results")
    ap.add_argument("--output_file", default=None)
    args = ap.parse_args()

    base = Path(args.input_root) / f"dataset={args.dataset_id}"
    metrics_files = sorted(base.glob("metric_set=*/criterion=*/judge_model=*/method=*/evidence=*/metrics.json"))
    if not metrics_files:
        print(f"No metrics files found under: {base}")
        return

    binary_rows: List[Dict[str, Any]] = []
    likert_rows: List[Dict[str, Any]] = []

    for p in metrics_files:
        meta = _extract_meta(p)
        criterion = meta.get("criterion", "")
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            continue
        is_binary = criterion.startswith("safety_c") or criterion == "composite_safety"
        for variation, metric in payload.items():
            row = _to_row(meta, variation, metric, "binary" if is_binary else "likert")
            if is_binary:
                binary_rows.append(row)
            else:
                likert_rows.append(row)

    os.makedirs(args.output_dir, exist_ok=True)
    output_name = args.output_file or f"exhaustive_metrics_{args.dataset_id}.xlsx"
    out_path = Path(args.output_dir) / output_name

    bdf = pd.DataFrame(binary_rows)
    ldf = pd.DataFrame(likert_rows)
    if not bdf.empty:
        bdf = bdf[["Model", "Method", "Criterion", "Evidence", "Variation"] + BINARY_COLUMNS]
    if not ldf.empty:
        ldf = ldf[["Model", "Method", "Criterion", "Evidence", "Variation"] + LIKERT_COLUMNS]

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        if not bdf.empty:
            bdf.to_excel(writer, sheet_name="binary_all_variations", index=False)
        if not ldf.empty:
            ldf.to_excel(writer, sheet_name="likert_all_variations", index=False)

    print(f"Binary rows: {len(bdf)}")
    print(f"Likert rows: {len(ldf)}")
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
