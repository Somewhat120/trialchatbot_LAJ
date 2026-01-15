#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
stage1_retrieve_consolidated.py
--------------------------------
Stage 1: Retrieval + Fusion + (Optional) Rerank → write frozen evidence context to JSONL.

Design goals
- Model-agnostic output: NO vLLM calls, NO prompt templates.
- One script supports Phase 1 + Phase 2 ablations via CLI flags.
- Resume-safe: appends JSONL; skips already-written keys unless --force.
- Deterministic: stable hashing for doc IDs + RRF tie-break.

Outputs
- JSONL where each line is a record for one (trial, question_num).
- Sidecar meta JSON describing settings.

Example runs
1) Hybrid RRF + Qwen reranker (CPU):
  python stage1_retrieve_consolidated.py \
    --input_csv data_preparation/raters.csv \
    --db_folder data_preparation/chroma_db_multiscale \
    --allowed_scales short \
    --retrieval_mode hybrid_rrf \
    --embedding_model Qwen/Qwen3-Embedding-8B \
    --reranker_type qwen \
    --qwen_reranker_model Qwen/Qwen3-Reranker-4B \
    --final_k 5 \
    --out_dir retrieval_cache/ \
    --max_workers 6

2) Hybrid RRF + BGE reranker (CPU):
  python stage1_retrieve_consolidated.py \
    --input_csv data_preparation/raters.csv \
    --db_folder data_preparation/chroma_db_multiscale \
    --allowed_scales short \
    --retrieval_mode hybrid_rrf \
    --embedding_model BAAI/bge-m3 \
    --reranker_type bge \
    --bge_reranker_model BAAI/bge-reranker-large \
    --final_k 5 \
    --out_dir retrieval_cache/ \
    --max_workers 6

3) Dense-only (no rerank):
  python stage1_retrieve_consolidated.py --retrieval_mode dense_only --reranker_type none

Notes on GPU usage
- If Stage 2 vLLM uses both GPUs (TP=2), keep Stage 1 embedding + reranking on CPU.
- If you dedicate a separate GPU job for Stage 1, you can set --embedding_device cuda and/or --reranker_device cuda.

"""

import os
# ---- CPU oversubscription guards (set BEFORE torch/transformers init) ----
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("MKL_NUM_THREADS", "2"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", os.getenv("OPENBLAS_NUM_THREADS", "2"))
os.environ.setdefault("NUMEXPR_NUM_THREADS", os.getenv("NUMEXPR_NUM_THREADS", "2"))

import json
import re
import argparse
import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

from rank_bm25 import BM25Okapi

# Optional dependencies for BGE reranker
try:
    from sentence_transformers import CrossEncoder
except Exception:
    CrossEncoder = None

# Optional deps for Qwen generative reranker
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False


# ---------------- locks for thread-safety ----------------
bm25_lock = threading.Lock()
write_lock = threading.Lock()


# ============================================================
# Helpers
# ============================================================

def safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def stable_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def word_tokenize(text: str) -> List[str]:
    # Fast tokenizer for BM25 (no NLTK).
    return re.findall(r"\b\w+\b", text.lower())


def resolve_question_num(idx: int, row: pd.Series) -> Optional[int]:
    qn = row.get("question_num", None)
    if qn is None:
        qn = row.get("question_id", None)
    if qn is not None:
        try:
            return int(float(qn)) if pd.notna(qn) else None
        except (ValueError, TypeError):
            return None
    try:
        return int(idx) + 1
    except Exception:
        return None


def normalize_trial_id(x: Any) -> str:
    s = safe_str(x).strip()
    if s.upper().startswith("NCT"):
        return s.upper()
    return "NCT" + s.replace(".0", "")


def load_existing_keys(out_jsonl: str) -> set:
    """Resume support: key = f"{normalized_id}::{question_num}"."""
    existing = set()
    if not os.path.exists(out_jsonl):
        return existing
    with open(out_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                nid = obj.get("normalized_id")
                qn = obj.get("question_num")
                if nid is not None and qn is not None:
                    existing.add(f"{nid}::{qn}")
            except Exception:
                continue
    return existing


def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


# ============================================================
# Rerankers
# ============================================================

class NullReranker:
    def rerank(self, pairs: List[Tuple[str, str]]) -> List[float]:
        # no rerank; return zeros
        return [0.0 for _ in pairs]


class BGEReranker:
    """Cross-encoder reranker (e.g., BAAI/bge-reranker-large)."""
    def __init__(self, model_name: str, device: str = "cpu"):
        if CrossEncoder is None:
            raise RuntimeError("sentence_transformers not available; cannot use bge reranker")
        self.model_name = model_name
        self.device = device
        self.model = CrossEncoder(model_name, device=device)

    def rerank(self, pairs: List[Tuple[str, str]]) -> List[float]:
        # CrossEncoder expects list of (query, doc)
        return [float(x) for x in self.model.predict(pairs)]


class Qwen3Reranker4B:
    """Generative reranker wrapper for Qwen/Qwen3-Reranker-4B."""

    def __init__(self, model_name: str, device: str = "cpu", max_length: int = 8192):
        if not _HAS_TORCH:
            raise RuntimeError("torch/transformers not available; cannot use qwen generative reranker")

        self.model_name = model_name
        self.device = device
        self.max_length = max_length

        print(f"⏳ Loading Qwen reranker: {model_name} on {device} ...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")

        if device == "cuda":
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=torch.float16,
                device_map="auto",
            ).eval()
        else:
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=torch.float32,
            ).to(device).eval()

        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")

        prefix = (
            "<|im_start|>system\n"
            "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
            "Note that the answer can only be \"yes\" or \"no\"."
            "<|im_end|>\n"
            "<|im_start|>user\n"
        )
        suffix = "<|im_end|>\n<|im_start|>assistant\n`<think>`\n\n`</think>`\n\n"

        self.prefix_tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(suffix, add_special_tokens=False)

        print("✅ Qwen reranker loaded")

    def _format(self, instruction: Optional[str], query: str, doc: str) -> str:
        if instruction is None:
            instruction = "Given a web search query, retrieve relevant passages that answer the query"
        return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}"

    def _process_inputs(self, formatted_pairs: List[str]) -> Dict[str, "torch.Tensor"]:
        inputs = self.tokenizer(
            formatted_pairs,
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens),
        )
        for i, ele in enumerate(inputs["input_ids"]):
            inputs["input_ids"][i] = self.prefix_tokens + ele + self.suffix_tokens

        inputs = self.tokenizer.pad(
            inputs,
            padding=True,
            return_tensors="pt",
            max_length=self.max_length,
        )
        for k in inputs:
            inputs[k] = inputs[k].to(self.model.device)
        return inputs

    @torch.no_grad()
    def _scores(self, inputs: Dict[str, "torch.Tensor"]) -> List[float]:
        logits = self.model(**inputs).logits[:, -1, :]
        true_vec = logits[:, self.token_true_id]
        false_vec = logits[:, self.token_false_id]
        two = torch.stack([false_vec, true_vec], dim=1)
        two = torch.nn.functional.log_softmax(two, dim=1)
        return two[:, 1].exp().tolist()  # p(yes)

    def rerank(self, pairs: List[Tuple[str, str]], instruction: Optional[str] = None) -> List[float]:
        formatted = [self._format(instruction, q, d) for (q, d) in pairs]
        inputs = self._process_inputs(formatted)
        return [float(x) for x in self._scores(inputs)]


# ============================================================
# Retrieval core
# ============================================================

def build_bm25_for_trial(db: Chroma, trial_id: str, allowed_scales: List[str]) -> Tuple[Optional[BM25Okapi], List[str], List[dict]]:
    where = {"$and": [{"nct_id": {"$eq": trial_id}}, {"scale": {"$in": allowed_scales}}]}
    docs = db.get(where=where, include=["documents", "metadatas"])
    if not docs["documents"]:
        return None, [], []
    tokenized = [word_tokenize(t) for t in docs["documents"]]
    return BM25Okapi(tokenized), docs["documents"], docs["metadatas"]


def get_bm25_cached(
    db: Chroma,
    trial_id: str,
    allowed_scales: List[str],
    bm25_cache: dict
) -> Tuple[Optional[BM25Okapi], List[str], List[dict]]:
    cache_key = f"{trial_id}::scales=" + ",".join(allowed_scales)
    with bm25_lock:
        cached = bm25_cache.get(cache_key)
    if cached is not None:
        return cached

    bm25, texts, metas = build_bm25_for_trial(db, trial_id, allowed_scales)

    with bm25_lock:
        bm25_cache[cache_key] = (bm25, texts, metas)
    return bm25, texts, metas


def reciprocal_rank_fusion(dense_docs: List[Document], sparse_docs: List[Document], k: int) -> List[Document]:
    scores: Dict[str, float] = {}
    doc_by_id: Dict[str, Document] = {}

    def add_ranked(docs: List[Document]):
        for rank, doc in enumerate(docs, start=1):
            did = stable_id(doc.page_content)
            doc_by_id[did] = doc
            scores[did] = scores.get(did, 0.0) + 1.0 / (k + rank)

    add_ranked(dense_docs)
    add_ranked(sparse_docs)

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [doc_by_id[did] for did, _ in ranked]


def dense_retrieve(db: Chroma, trial_id: str, query: str, allowed_scales: List[str], dense_k: int) -> List[Document]:
    filt = {"$and": [{"nct_id": {"$eq": trial_id}}, {"scale": {"$in": allowed_scales}}]}
    return db.similarity_search(query, k=dense_k, filter=filt)


def bm25_retrieve(db: Chroma, trial_id: str, query: str, allowed_scales: List[str], bm25_k: int, bm25_cache: dict) -> List[Document]:
    bm25, texts, metas = get_bm25_cached(db, trial_id, allowed_scales, bm25_cache)
    if not bm25:
        return []
    scores = bm25.get_scores(word_tokenize(query))
    top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:bm25_k]
    return [Document(page_content=texts[i], metadata=metas[i] if i < len(metas) else {}) for i in top_idx]


def hybrid_retrieve(
    db: Chroma,
    trial_id: str,
    query: str,
    allowed_scales: List[str],
    dense_k: int,
    bm25_k: int,
    rrf_k: int,
    bm25_cache: dict,
    retrieval_mode: str,
) -> List[Document]:

    if retrieval_mode == "dense_only":
        return dense_retrieve(db, trial_id, query, allowed_scales, dense_k)

    if retrieval_mode == "bm25_only":
        return bm25_retrieve(db, trial_id, query, allowed_scales, bm25_k, bm25_cache)

    # hybrid_rrf
    dense_docs = dense_retrieve(db, trial_id, query, allowed_scales, dense_k)
    sparse_docs = bm25_retrieve(db, trial_id, query, allowed_scales, bm25_k, bm25_cache)

    if dense_docs and sparse_docs:
        return reciprocal_rank_fusion(dense_docs, sparse_docs, k=rrf_k)
    return dense_docs if dense_docs else sparse_docs


# ============================================================
# Rerank + select
# ============================================================

def rerank_and_select(
    candidates: List[Document],
    question: str,
    answer: str,
    reranker,
    reranker_type: str,
    final_k: int,
    reranker_batch_size: int,
    max_doc_chars_rerank: int,
) -> Tuple[str, List[dict]]:
    """Rerank using combined (question + answer) signal.

    - Truncates docs for scoring only.
    - Final context contains full chunk text (untruncated).
    """
    if not candidates:
        return "", []

    full_query = f"Question: {question}\n\nAnswer to evaluate: {answer}"

    # No rerank: keep candidate order and take top-K
    if reranker_type == "none" or isinstance(reranker, NullReranker):
        top = candidates[:final_k]
        ctx_parts = []
        meta = []
        for i, doc in enumerate(top):
            scale = doc.metadata.get("scale", "unknown")
            ctx_parts.append(f"=== [EVIDENCE {i+1} (Type: {scale})] ===\n{doc.page_content}")
            meta.append({
                "chunk_index": i + 1,
                "scale": scale,
                "rerank_score": None,
                "metadata": doc.metadata if isinstance(doc.metadata, dict) else {},
            })
        return "\n\n".join(ctx_parts), meta

    # With rerank
    all_scores: List[float] = []
    for i in range(0, len(candidates), reranker_batch_size):
        batch = candidates[i:i + reranker_batch_size]
        pairs = [(full_query, d.page_content[:max_doc_chars_rerank]) for d in batch]
        try:
            batch_scores = reranker.rerank(pairs)
            all_scores.extend(batch_scores)
        except Exception as e:
            print(f"⚠️ Rerank batch {i // reranker_batch_size + 1} failed: {e}")
            all_scores.extend([0.1] * len(batch))

    ranked = sorted(zip(candidates, all_scores), key=lambda x: x[1], reverse=True)[:final_k]

    ctx_parts: List[str] = []
    meta: List[dict] = []
    for j, (doc, score) in enumerate(ranked):
        scale = doc.metadata.get("scale", "unknown")
        ctx_parts.append(
            f"=== [EVIDENCE {j+1} (Type: {scale}, Rerank Score: {score:.4f})] ===\n{doc.page_content}"
        )
        meta.append({
            "chunk_index": j + 1,
            "scale": scale,
            "rerank_score": float(score),
            "metadata": doc.metadata if isinstance(doc.metadata, dict) else {},
        })

    return "\n\n".join(ctx_parts), meta


# ============================================================
# Stage 1 worker
# ============================================================

def process_one(
    idx: int,
    row: pd.Series,
    db: Chroma,
    reranker,
    bm25_cache: dict,
    allowed_scales: List[str],
    retrieval_mode: str,
    dense_k: int,
    bm25_k: int,
    rrf_k: int,
    final_k: int,
    reranker_type: str,
    reranker_batch_size: int,
    max_doc_chars_rerank: int,
) -> Dict[str, Any]:

    normalized_id = normalize_trial_id(row.get("trial_num"))
    q = safe_str(row.get("question"))
    a = safe_str(row.get("answer"))
    human_score = row.get("Level of factual accuracy")

    question_num = resolve_question_num(idx, row)

    # retrieval query includes both Q + A (judge context)
    query = f"{q} {a}".strip()

    candidates = hybrid_retrieve(
        db=db,
        trial_id=normalized_id,
        query=query,
        allowed_scales=allowed_scales,
        dense_k=dense_k,
        bm25_k=bm25_k,
        rrf_k=rrf_k,
        bm25_cache=bm25_cache,
        retrieval_mode=retrieval_mode,
    )

    if not candidates:
        return {
            "status": "no_candidates",
            "question_num": question_num,
            "trial_num": row.get("trial_num"),
            "normalized_id": normalized_id,
            "question": q,
            "chatbot_answer": a,
            "human_score": human_score,
            "retrieved_context": "",
            "retrieval_meta": [],
        }

    ctx, meta = rerank_and_select(
        candidates=candidates,
        question=q,
        answer=a,
        reranker=reranker,
        reranker_type=reranker_type,
        final_k=final_k,
        reranker_batch_size=reranker_batch_size,
        max_doc_chars_rerank=max_doc_chars_rerank,
    )

    return {
        "status": "ok",
        "question_num": question_num,
        "trial_num": row.get("trial_num"),
        "normalized_id": normalized_id,
        "question": q,
        "chatbot_answer": a,
        "human_score": human_score,
        "retrieved_context": ctx,
        "retrieval_meta": meta,
    }


# ============================================================
# CLI + main
# ============================================================

@dataclass
class OutputSpec:
    out_jsonl: str
    meta_json: str


def slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()


def default_output_name(args) -> OutputSpec:
    # automatic, descriptive naming
    scales = "-".join(args.allowed_scales)
    emb = slug(args.embedding_model)

    if args.reranker_type == "none":
        rr = "rerank_none"
    elif args.reranker_type == "bge":
        rr = "rerank_bge_" + slug(args.bge_reranker_model)
    else:
        rr = "rerank_qwen_" + slug(args.qwen_reranker_model)

    fname = f"stage1_{args.retrieval_mode}_scales-{scales}_emb-{emb}_{rr}_finalk-{args.final_k}.jsonl"
    out_jsonl = os.path.join(args.out_dir, fname)
    meta_json = out_jsonl + ".meta.json"
    return OutputSpec(out_jsonl=out_jsonl, meta_json=meta_json)


def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--input_csv", type=str, default=os.getenv("INPUT_CSV", "data_preparation/raters.csv"))
    p.add_argument("--db_folder", type=str, default=os.getenv("DB_FOLDER", "data_preparation/chroma_db_multiscale"))
    p.add_argument("--out_dir", type=str, default=os.getenv("OUT_DIR", "retrieval_cache"))
    p.add_argument("--out_jsonl", type=str, default=os.getenv("OUT_JSONL", ""), help="Optional explicit path; overrides auto naming.")

    p.add_argument("--test_mode", action="store_true")
    p.add_argument("--test_n", type=int, default=int(os.getenv("TEST_N", "10")))

    p.add_argument("--force", action="store_true", help="Overwrite output (disables resume).")
    p.add_argument("--max_workers", type=int, default=int(os.getenv("MAX_WORKERS", "6")))

    p.add_argument("--allowed_scales", nargs="+", default=os.getenv("ALLOWED_SCALES", "medium").split(","))

    p.add_argument("--retrieval_mode", choices=["dense_only", "bm25_only", "hybrid_rrf"], default=os.getenv("RETRIEVAL_MODE", "hybrid_rrf"))
    p.add_argument("--dense_k", type=int, default=int(os.getenv("DENSE_K", "10")))
    p.add_argument("--bm25_k", type=int, default=int(os.getenv("BM25_K", "10")))
    p.add_argument("--rrf_k", type=int, default=int(os.getenv("RRF_K", "60")))

    # IMPORTANT: you asked to retrieve top-5
    p.add_argument("--final_k", type=int, default=int(os.getenv("FINAL_K", "4")))

    p.add_argument("--embedding_model", type=str, default=os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"))
    p.add_argument("--embedding_device", type=str, default=os.getenv("EMBEDDING_DEVICE", "cpu"))

    p.add_argument("--reranker_type", choices=["none", "qwen", "bge"], default=os.getenv("RERANKER_TYPE", "qwen"))
    p.add_argument("--reranker_device", type=str, default=os.getenv("RERANKER_DEVICE", "cpu"))
    p.add_argument("--reranker_batch_size", type=int, default=int(os.getenv("RERANKER_BATCH_SIZE", "3")))
    p.add_argument("--max_doc_chars_rerank", type=int, default=int(os.getenv("MAX_DOC_CHARS_RERANK", "4000")))

    # qwen reranker params
    p.add_argument("--qwen_reranker_model", type=str, default=os.getenv("QWEN_RERANKER_MODEL", "Qwen/Qwen3-Reranker-4B"))

    # bge reranker params
    p.add_argument("--bge_reranker_model", type=str, default=os.getenv("BGE_RERANKER_MODEL", "BAAI/bge-m3"))

    return p.parse_args()


def main():
    args = parse_args()

    ensure_dir(args.out_dir)

    # Determine output paths
    if args.out_jsonl.strip():
        out_jsonl = args.out_jsonl
        meta_json = out_jsonl + ".meta.json"
    else:
        spec = default_output_name(args)
        out_jsonl, meta_json = spec.out_jsonl, spec.meta_json

    # Output handling
    if args.force and os.path.exists(out_jsonl):
        os.remove(out_jsonl)

    existing_keys = set()
    if not args.force:
        existing_keys = load_existing_keys(out_jsonl)
        if existing_keys:
            print(f"↩️ Resume enabled: found {len(existing_keys)} existing rows in {out_jsonl}")

    trial_index_path = os.path.join(args.db_folder, "available_trials.json")
    with open(trial_index_path, "r", encoding="utf-8") as f:
        valid_ids = set(json.load(f))

    # Load embeddings + DB
    print(f"⏳ Loading embeddings on {args.embedding_device}: {args.embedding_model}")
    embedding_fn = HuggingFaceEmbeddings(
        model_name=args.embedding_model,
        model_kwargs={"trust_remote_code": True, "device": args.embedding_device},
    )
    db = Chroma(persist_directory=args.db_folder, embedding_function=embedding_fn)
    print("✅ Chroma DB loaded")

    # Initialize reranker
    if args.reranker_type == "none":
        reranker = NullReranker()
        reranker_name = "none"
    elif args.reranker_type == "bge":
        if CrossEncoder is None:
            raise RuntimeError("sentence_transformers not installed; cannot use bge reranker")
        reranker = BGEReranker(model_name=args.bge_reranker_model, device=args.reranker_device)
        reranker_name = args.bge_reranker_model
    else:
        reranker = Qwen3Reranker4B(model_name=args.qwen_reranker_model, device=args.reranker_device)
        reranker_name = args.qwen_reranker_model

    # Load data
    df = pd.read_csv(args.input_csv)
    if args.test_mode:
        df = df.head(args.test_n)

    df["normalized_id"] = df["trial_num"].apply(normalize_trial_id)
    df = df[df["normalized_id"].isin(valid_ids)].reset_index(drop=True)

    # Worklist (resume-aware)
    work_items: List[Tuple[int, pd.Series]] = []
    for i, row in df.iterrows():
        qn = resolve_question_num(i, row)
        nid = row["normalized_id"]
        key = f"{nid}::{qn}"
        if (not args.force) and (key in existing_keys):
            continue
        work_items.append((i, row))

    print(f"📌 Input rows after valid_id filter: {len(df)}")
    print(f"📌 Rows to process this run: {len(work_items)}")
    print(
        "⚙️  "
        f"retrieval_mode={args.retrieval_mode} | allowed_scales={args.allowed_scales} | "
        f"dense_k={args.dense_k} | bm25_k={args.bm25_k} | rrf_k={args.rrf_k} | final_k={args.final_k}"
    )
    print(
        "⚙️  "
        f"embedding={args.embedding_model} ({args.embedding_device}) | "
        f"reranker_type={args.reranker_type} | reranker={reranker_name} ({args.reranker_device}) | "
        f"max_doc_chars_rerank={args.max_doc_chars_rerank}"
    )
    print(f"⚙️  max_workers={args.max_workers}")
    print(f"📝 Output JSONL: {out_jsonl}")

    # BM25 cache lives for the entire run (thread-safe)
    bm25_cache: dict = {}

    # Write meta sidecar
    meta_obj = {
        "input_csv": args.input_csv,
        "db_folder": args.db_folder,
        "retrieval_mode": args.retrieval_mode,
        "allowed_scales": args.allowed_scales,
        "dense_k": args.dense_k,
        "bm25_k": args.bm25_k,
        "rrf_k": args.rrf_k,
        "final_k": args.final_k,
        "embedding_model": args.embedding_model,
        "embedding_device": args.embedding_device,
        "reranker_type": args.reranker_type,
        "reranker_model": reranker_name,
        "reranker_device": args.reranker_device,
        "reranker_batch_size": args.reranker_batch_size,
        "max_doc_chars_rerank": args.max_doc_chars_rerank,
    }
    with open(meta_json, "w", encoding="utf-8") as mf:
        json.dump(meta_obj, mf, indent=2, ensure_ascii=False)

    # Ensure output dir exists
    ensure_dir(os.path.dirname(out_jsonl) or ".")

    def write_jsonl(obj: Dict[str, Any]):
        with write_lock:
            with open(out_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # Process
    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = []
        for (i, row) in work_items:
            futures.append(
                ex.submit(
                    process_one,
                    i,
                    row,
                    db,
                    reranker,
                    bm25_cache,
                    args.allowed_scales,
                    args.retrieval_mode,
                    args.dense_k,
                    args.bm25_k,
                    args.rrf_k,
                    args.final_k,
                    args.reranker_type,
                    args.reranker_batch_size,
                    args.max_doc_chars_rerank,
                )
            )

        for fut in tqdm(as_completed(futures), total=len(futures), desc="stage1_retrieve"):
            obj = fut.result()
            # attach settings per record for provenance
            obj["retrieval_settings"] = {
                "retrieval_mode": args.retrieval_mode,
                "allowed_scales": args.allowed_scales,
                "dense_k": args.dense_k,
                "bm25_k": args.bm25_k,
                "rrf_k": args.rrf_k,
                "final_k": args.final_k,
                "embedding_model": args.embedding_model,
                "reranker_type": args.reranker_type,
                "reranker_model": reranker_name,
                "max_doc_chars_rerank": args.max_doc_chars_rerank,
            }
            write_jsonl(obj)

    print(f"✅ Stage 1 complete → {out_jsonl}")
    print(f"✅ Metadata saved → {meta_json}")


if __name__ == "__main__":
    main()
