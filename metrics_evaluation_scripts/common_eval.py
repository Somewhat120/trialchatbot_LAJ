#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import glob
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    mean_absolute_error,
    mean_squared_error,
)

CANONICAL_METHODS = {
    "agent": {"agent"},
    "dspy_opt_prompt": {"dspy_opt_prompt"},
    "prompt_fewshot": {"baseline", "def_only", "def_cot"},
    "prompt_zeroshot": {"baseline", "def_only", "def_cot"},
}

CANONICAL_EVIDENCE = {"no_context", "oracle_full_text", "icf_chunks_k5", "no_rag_control"}

CRITERION_BY_FILENAME = {
    "c1_critical_harm.jsonl": "safety_c1_critical_harm",
    "c1_critical_harm_v1.jsonl": "safety_c1_critical_harm",
    "c1_critical_harm_v2.jsonl": "safety_c1_critical_harm",
    "c2_individualized_medical_advice.jsonl": "safety_c2_individualized_medical_advice",
    "c2_individualized_medical_advice_v1.jsonl": "safety_c2_individualized_medical_advice",
    "c2_individualized_medical_advice_v2.jsonl": "safety_c2_individualized_medical_advice",
    "c3_inappropriate_persuasion.jsonl": "safety_c3_inappropriate_persuasion",
    "c3_inappropriate_persuasion_v1.jsonl": "safety_c3_inappropriate_persuasion",
    "c3_inappropriate_persuasion_v2.jsonl": "safety_c3_inappropriate_persuasion",
    "c4_transparency.jsonl": "reliability_c4_transparency",
    "c5_factual_accuracy.jsonl": "reliability_c5_factual_accuracy",
    "c6_usefulness.jsonl": "communication_c6_usefulness",
    "c7_clarity.jsonl": "communication_c7_clarity_succinctness",
}

BINARY_CRITERIA = {
    "safety_c1_critical_harm",
    "safety_c2_individualized_medical_advice",
    "safety_c3_inappropriate_persuasion",
    "composite_safety",
}

LIKERT_CRITERIA = {
    "reliability_c4_transparency",
    "reliability_c5_factual_accuracy",
    "communication_c6_usefulness",
    "communication_c7_clarity_succinctness",
}

BOOTSTRAP_METHOD = "percentile"
DEFAULT_BOOTSTRAP_REPS = 5000
DEFAULT_BOOTSTRAP_SEED = 42


@dataclass(frozen=True)
class EvalConfig:
    criterion_id: str
    judge_model: str
    method: str
    variation: str
    evidence: str
    source_file: str


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_records(path: str) -> List[Dict[str, Any]]:
    if path.endswith(".jsonl"):
        rows: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, list) else []
    return []


def write_json(path: str, payload: Any) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def discover_score_files(input_root: str) -> List[str]:
    return sorted(glob.glob(os.path.join(input_root, "**", "*.jsonl"), recursive=True))


def parse_kv_from_path(path: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    for token in os.path.normpath(path).split(os.sep):
        if "=" in token:
            k, v = token.split("=", 1)
            if k and v:
                kv[k] = v
    return kv


def infer_criterion_from_filename(path: str) -> Optional[str]:
    return CRITERION_BY_FILENAME.get(os.path.basename(path))


def normalize_evidence_for_composite(evidence: str) -> str:
    """Map path evidence tags to a single composite output stratum (no RAG control ≡ no context)."""
    if evidence == "no_rag_control":
        return "no_context"
    return evidence


def strict_eval_config_from_path(path: str) -> Optional[EvalConfig]:
    criterion_id = infer_criterion_from_filename(path)
    if not criterion_id:
        return None
    kv = parse_kv_from_path(path)
    model = kv.get("model")
    method = kv.get("method")
    variation = kv.get("variation")
    evidence = kv.get("evidence")
    if not all([model, method, variation, evidence]):
        return None
    if method not in CANONICAL_METHODS:
        return None
    if variation not in CANONICAL_METHODS[method]:
        return None
    if evidence not in CANONICAL_EVIDENCE:
        return None
    return EvalConfig(
        criterion_id=criterion_id,
        judge_model=model,
        method=method,
        variation=variation,
        evidence=evidence,
        source_file=path,
    )


def canonical_id_column(df: pd.DataFrame) -> pd.Series:
    if "qa_id" in df.columns:
        return df["qa_id"]
    if "record_id" in df.columns:
        return df["record_id"]
    return pd.Series([None] * len(df), index=df.index)


def score_frame_from_records(
    records: Sequence[Dict[str, Any]],
    criterion_id: str,
) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["id", "human_score", "ai_score"])
    df = pd.DataFrame(records)
    if "human_score" not in df.columns or "ai_score" not in df.columns:
        return pd.DataFrame(columns=["id", "human_score", "ai_score"])
    df = df.copy()
    df["id"] = canonical_id_column(df)
    df["human_score"] = pd.to_numeric(df["human_score"], errors="coerce")
    df["ai_score"] = pd.to_numeric(df["ai_score"], errors="coerce")
    df = df.dropna(subset=["id", "human_score", "ai_score"])
    if criterion_id in BINARY_CRITERIA:
        df["human_score"] = df["human_score"].astype(int)
        df["ai_score"] = df["ai_score"].astype(int)
        df = df[df["human_score"].isin([0, 1]) & df["ai_score"].isin([0, 1])]
    else:
        df["human_score"] = df["human_score"].astype(int)
        df["ai_score"] = df["ai_score"].astype(int)
        df = df[df["human_score"].between(1, 5) & df["ai_score"].between(1, 5)]
    df = df.drop_duplicates(subset=["id"], keep="first")
    return df[["id", "human_score", "ai_score"]].reset_index(drop=True)


def gwet_ac1_binary(tn: int, fp: int, fn: int, tp: int) -> float:
    n = tn + fp + fn + tp
    if n == 0:
        return 0.0
    p_a = (tp + tn) / n
    pi_1 = (2 * tp + fp + fn) / (2 * n)
    p_e = 2 * pi_1 * (1 - pi_1) if 0 <= pi_1 <= 1 else 0.0
    if p_e >= 1.0:
        return 0.0
    return float((p_a - p_e) / (1 - p_e))


def _safe_div(num: float, den: float) -> float:
    return float(num / den) if den else 0.0


def _binary_sensitivity(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()
    return _safe_div(tp, tp + fn)


def _binary_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(f1_score(y_true, y_pred, average="binary", zero_division=0))


def _ordinal_spearman(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    v = spearmanr(y_true, y_pred).correlation
    if v is None or np.isnan(v):
        return 0.0
    return float(v)


def _ordinal_within1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float((np.abs(y_true - y_pred) <= 1).mean())


def bootstrap_ci(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    metric_fn: Callable[[np.ndarray, np.ndarray], float],
    reps: int = DEFAULT_BOOTSTRAP_REPS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    n = len(y_true)
    if n == 0:
        return {
            "low": None,
            "high": None,
            "method": BOOTSTRAP_METHOD,
            "reps": reps,
            "seed": seed,
            "unit": "qa_pair",
        }
    rng = np.random.default_rng(seed)
    vals = np.empty(reps, dtype=float)
    idx_base = np.arange(n)
    for i in range(reps):
        idx = rng.choice(idx_base, size=n, replace=True)
        vals[i] = metric_fn(y_true[idx], y_pred[idx])
    low, high = np.percentile(vals, [2.5, 97.5])
    return {
        "low": float(low),
        "high": float(high),
        "method": BOOTSTRAP_METHOD,
        "reps": reps,
        "seed": seed,
        "unit": "qa_pair",
    }


def calculate_binary_metrics(
    df: pd.DataFrame,
    bootstrap_reps: int = DEFAULT_BOOTSTRAP_REPS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    y_true = df["human_score"].to_numpy(dtype=int)
    y_pred = df["ai_score"].to_numpy(dtype=int)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()
    sensitivity = _safe_div(tp, tp + fn)
    specificity = _safe_div(tn, tn + fp)
    precision = _safe_div(tp, tp + fp)
    percent_agreement = float(accuracy_score(y_true, y_pred))
    f1 = _binary_f1(y_true, y_pred)
    mcc = float(matthews_corrcoef(y_true, y_pred))
    balanced = float(balanced_accuracy_score(y_true, y_pred))
    ac1 = gwet_ac1_binary(int(tn), int(fp), int(fn), int(tp))
    kappa = float(cohen_kappa_score(y_true, y_pred))
    sens_ci = bootstrap_ci(y_true, y_pred, _binary_sensitivity, bootstrap_reps, bootstrap_seed)
    f1_ci = bootstrap_ci(y_true, y_pred, _binary_f1, bootstrap_reps, bootstrap_seed)
    return {
        "n": int(len(df)),
        "analysis_unit": "qa_pair",
        "sensitivity": sensitivity,
        "f1": f1,
        "specificity": specificity,
        "precision": precision,
        "mcc": mcc,
        "gwet_ac1": ac1,
        "cohen_kappa": kappa,
        "percent_agreement": percent_agreement,
        "balanced_accuracy": balanced,
        "sensitivity_ci": sens_ci,
        "f1_ci": f1_ci,
        "confusion_matrix": {"TN": int(tn), "FP": int(fp), "FN": int(fn), "TP": int(tp)},
        # legacy compatibility aliases
        "accuracy": percent_agreement,
        "sensitivity_recall": sensitivity,
        "ac1": ac1,
        "kappa": kappa,
    }


def calculate_ordinal_metrics(
    df: pd.DataFrame,
    bootstrap_reps: int = DEFAULT_BOOTSTRAP_REPS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    y_true = df["human_score"].to_numpy(dtype=int)
    y_pred = df["ai_score"].to_numpy(dtype=int)
    pear = pearsonr(y_true, y_pred)[0] if len(df) > 1 else 0.0
    spear = _ordinal_spearman(y_true, y_pred)
    exact = float((y_true == y_pred).mean())
    within1 = _ordinal_within1(y_true, y_pred)
    k_std = float(cohen_kappa_score(y_true, y_pred, weights=None))
    k_lin = float(cohen_kappa_score(y_true, y_pred, weights="linear"))
    k_quad = float(cohen_kappa_score(y_true, y_pred, weights="quadratic"))
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    spear_ci = bootstrap_ci(y_true, y_pred, _ordinal_spearman, bootstrap_reps, bootstrap_seed)
    within1_ci = bootstrap_ci(y_true, y_pred, _ordinal_within1, bootstrap_reps, bootstrap_seed)
    return {
        "n": int(len(df)),
        "analysis_unit": "qa_pair",
        "spearman_rho": spear,
        "within_1_point": within1,
        "exact_match": exact,
        "weighted_kappa_standard": k_std,
        "weighted_kappa_linear": k_lin,
        "weighted_kappa_quadratic": k_quad,
        "mae": mae,
        "rmse": rmse,
        "pearson_r": float(0.0 if np.isnan(pear) else pear),
        "spearman_rho_ci": spear_ci,
        "within_1_point_ci": within1_ci,
        # legacy compatibility structure
        "correlations": {"pearson": float(0.0 if np.isnan(pear) else pear), "spearman": spear},
        "accuracy": {"exact_match": exact, "within_1_point": within1},
        "reliability": {
            "kappa_standard": k_std,
            "kappa_linear": k_lin,
            "kappa_quadratic": k_quad,
        },
        "error": {"mae": mae, "rmse": rmse},
    }
