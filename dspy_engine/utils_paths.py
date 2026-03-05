#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
dspy_engine/utils_paths.py

Utility functions for resolving paths to compiled DSPy artifacts.
Used by skill rewriting (dspy_opt_prompt) and other optimization methods.
"""

import os
from typing import Optional


def resolve_latest_best_skill_path(
    compiled_root: str,
    criterion_id: str,
    skill_version: str,
    judge_model: str,
    depth: int = 1,
    candidates: int = 3,
    rewriter_model: Optional[str] = None,
    backend: str = "openai",
) -> Optional[str]:
    """
    Resolve the path to the latest best_skill.md for a given (criterion_id, skill_version,
    judge_model, depth, candidates). Use this when the runner does not know the exact run= timestamp.

    Directory layout under compiled_root (must match compile_dspy_copro_skill_*.build_artifact_dir):
      criterion={id}/skill_version={v}/backend={backend}/judge_model={M}/rewriter_model={R}/
        optimizer=copro_skill/depth={d}/candidates={n}/run={stamp}/best_skill.md

    Returns the full path to best_skill.md in the latest run, or None if no run exists.
    """
    rewriter = rewriter_model if rewriter_model is not None else judge_model
    base = os.path.join(
        compiled_root,
        f"criterion={criterion_id}",
        f"skill_version={skill_version}",
        f"backend={backend}",
        f"judge_model={judge_model}",
        f"rewriter_model={rewriter}",
        "optimizer=copro_skill",
        f"depth={depth}",
        f"candidates={candidates}",
    )
    if not os.path.isdir(base):
        return None
    run_dirs = [d for d in os.listdir(base) if d.startswith("run=")]
    if not run_dirs:
        return None
    run_dirs.sort(reverse=True)
    latest_run = run_dirs[0]
    path = os.path.join(base, latest_run, "best_skill.md")
    return path if os.path.isfile(path) else None

