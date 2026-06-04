#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
01_retrieval_chunk.py
------------------------
Stage 1: Retrieval + Fusion + Rerank → write frozen evidence context to JSONL.

Design goals
- Model-agnostic output: NO vLLM calls, NO prompt templates.
- Resume-safe: appends JSONL; skips already-written keys unless --force.
- Deterministic: stable hashing for doc IDs + RRF tie-break.
- Q-only retrieval for unbiased fact-checking (answer used only in reranking).
- Chunk deduplication before reranking.

Supported Experimental Arms:
1) Naive (BM25 only)
2) Hybrid RRF (BM25 + Dense)
3) Hybrid + mxbai (Cross-Encoder)
4) Hybrid + Qwen (Generative Reranker)
"""

import os
# ---- CPU oversubscription guards (set BEFORE torch/transformers init) ----
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("MKL_NUM_THREADS", "2"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", os.getenv("OPENBLAS_NUM_THREADS", "2"))
os.environ.setdefault("NUMEXPR_NUM_THREADS", os.getenv("NUMEXPR_NUM_THREADS", "2"))
# Disable chat template fetching for embedding models (fixes transformers 4.44+ issue)
os.environ.setdefault("HF_HUB_DISABLE_EXPERIMENTAL_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

import json
import re
import argparse
import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

from rank_bm25 import BM25Okapi

# Optional dependencies for CrossEncoder rerankers (BGE, mxbai)
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

# Optional: for version check
try:
    import huggingface_hub
    _HAS_HF_HUB = True
except Exception:
    _HAS_HF_HUB = False


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

def normalize_trial_id(x: Any) -> Optional[str]:
    """Normalize trial ID. Returns None if input is null/empty."""
    if x is None:
        return None
    s = safe_str(x).strip()
    if not s or s.lower() == "null" or s.lower() == "none":
        return None
    if s.upper().startswith("NCT"):
        return s.upper()
    return "NCT" + s.replace(".0", "")

def load_existing_keys(out_jsonl: str) -> set:
    """Resume support: key = qa_id (as integer or string)."""
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
                qa_id = obj.get("qa_id")
                if qa_id is not None:
                    existing.add(int(qa_id))
                    existing.add(str(qa_id))
            except Exception:
                continue
    return existing

def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def deduplicate_chunks(candidates: List[Document]) -> List[Document]:
    """Remove duplicate chunks based on content hash before reranking."""
    seen = set()
    unique = []
    for doc in candidates:
        doc_id = stable_id(doc.page_content)
        if doc_id not in seen:
            seen.add(doc_id)
            unique.append(doc)
    return unique


# ============================================================
# Rerankers
# ============================================================

class NullReranker:
    def rerank(self, pairs: List[Tuple[str, str]]) -> List[float]:
        # no rerank; return zeros
        return [0.0 for _ in pairs]

class CrossEncoderReranker:
    """Cross-encoder reranker for models like mxbai-rerank-large-v2 or bge-reranker."""
    def __init__(self, model_name: str, device: str = "cpu"):
        if CrossEncoder is None:
            raise RuntimeError("sentence_transformers not available; cannot use cross-encoder reranker")
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
# Retrieval core (UPDATED: No 'scale' filter)
# ============================================================

def build_bm25_for_trial(db: Chroma, trial_id: str) -> Tuple[Optional[BM25Okapi], List[str], List[dict]]:
    where = {"nct_id": {"$eq": trial_id}}
    docs = db.get(where=where, include=["documents", "metadatas"])
    if not docs["documents"]:
        return None, [], []
    tokenized = [word_tokenize(t) for t in docs["documents"]]
    return BM25Okapi(tokenized), docs["documents"], docs["metadatas"]

def get_bm25_cached(
    db: Chroma,
    trial_id: str,
    bm25_cache: dict
) -> Tuple[Optional[BM25Okapi], List[str], List[dict]]:
    cache_key = f"{trial_id}::bm25"
    with bm25_lock:
        cached = bm25_cache.get(cache_key)
    if cached is not None:
        return cached

    bm25, texts, metas = build_bm25_for_trial(db, trial_id)

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

def dense_retrieve(db: Chroma, trial_id: str, query: str, dense_k: int) -> List[Document]:
    filt = {"nct_id": {"$eq": trial_id}}
    return db.similarity_search(query, k=dense_k, filter=filt)

def bm25_retrieve(db: Chroma, trial_id: str, query: str, bm25_k: int, bm25_cache: dict) -> List[Document]:
    bm25, texts, metas = get_bm25_cached(db, trial_id, bm25_cache)
    if not bm25:
        return []
    scores = bm25.get_scores(word_tokenize(query))
    top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:bm25_k]
    return [Document(page_content=texts[i], metadata=metas[i] if i < len(metas) else {}) for i in top_idx]

def hybrid_retrieve(
    db: Chroma,
    trial_id: str,
    query: str,
    dense_k: int,
    bm25_k: int,
    rrf_k: int,
    bm25_cache: dict,
    retrieval_mode: str,
) -> List[Document]:

    if retrieval_mode == "dense_only":
        return dense_retrieve(db, trial_id, query, dense_k)

    if retrieval_mode == "bm25_only":
        return bm25_retrieve(db, trial_id, query, bm25_k, bm25_cache)

    # hybrid_rrf
    dense_docs = dense_retrieve(db, trial_id, query, dense_k)
    sparse_docs = bm25_retrieve(db, trial_id, query, bm25_k, bm25_cache)

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
    """Rerank using combined (question + answer) signal."""
    if not candidates:
        return "", []

    full_query = f"Question: {question}\n\nAnswer to evaluate: {answer}"

    # No rerank: keep candidate order and take top-K
    if reranker_type == "none" or isinstance(reranker, NullReranker):
        top = candidates[:final_k]
        ctx_parts = []
        meta = []
        for i, doc in enumerate(top):
            # Swapped 'scale' for 'semantic_tag' to match the new DB
            tag = doc.metadata.get("semantic_tag", "unknown")
            ctx_parts.append(f"=== [EVIDENCE {i+1} (Type: {tag})] ===\n{doc.page_content}")
            meta.append({
                "chunk_index": i + 1,
                "semantic_tag": tag,
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
        tag = doc.metadata.get("semantic_tag", "unknown")
        ctx_parts.append(
            f"=== [EVIDENCE {j+1} (Type: {tag}, Rerank Score: {score:.4f})] ===\n{doc.page_content}"
        )
        meta.append({
            "chunk_index": j + 1,
            "semantic_tag": tag,
            "rerank_score": float(score),
            "metadata": doc.metadata if isinstance(doc.metadata, dict) else {},
        })

    return "\n\n".join(ctx_parts), meta


# ============================================================
# Stage 1 worker
# ============================================================

def process_one(
    rec: Dict[str, Any],
    db: Optional[Chroma],
    reranker,
    bm25_cache: dict,
    retrieval_mode: str,
    dense_k: int,
    bm25_k: int,
    rrf_k: int,
    final_k: int,
    reranker_type: str,
    reranker_batch_size: int,
    max_doc_chars_rerank: int,
    valid_trial_ids: set,
) -> Dict[str, Any]:
    """Process one QA pair. Returns output record with qa_id."""

    qa_id = rec.get("qa_id")
    question_type = rec.get("question_type", "").upper()
    trial_num = rec.get("trial_num")
    normalized_id = normalize_trial_id(trial_num)
    
    q = safe_str(rec.get("question", ""))
    a = safe_str(rec.get("answer", ""))
    human_score = rec.get("Level of factual accuracy")
    question_num_raw_source = rec.get("question_num_raw_source")

    # Handle GENERAL questions (no trial-specific evidence available)
    if question_type == "GENERAL" or normalized_id is None:
        return {
            "status": "no_evidence_available",
            "qa_id": qa_id,
            "question_num_raw_source": question_num_raw_source,
            "trial_num": trial_num,
            "normalized_id": normalized_id,
            "question_type": question_type,
            "question": q,
            "chatbot_answer": a,
            "human_score": human_score,
            "retrieved_context": "",
            "retrieval_meta": [],
        }

    # Check if trial is in valid set
    if normalized_id not in valid_trial_ids:
        return {
            "status": "trial_not_in_db",
            "qa_id": qa_id,
            "question_num_raw_source": question_num_raw_source,
            "trial_num": trial_num,
            "normalized_id": normalized_id,
            "question_type": question_type,
            "question": q,
            "chatbot_answer": a,
            "human_score": human_score,
            "retrieved_context": "",
            "retrieval_meta": [],
        }

    # QA query retrieval (question + answer)
    query = f"{q} {a}".strip()

    candidates = hybrid_retrieve(
        db=db,
        trial_id=normalized_id,
        query=query,
        dense_k=dense_k,
        bm25_k=bm25_k,
        rrf_k=rrf_k,
        bm25_cache=bm25_cache,
        retrieval_mode=retrieval_mode,
    )

    if not candidates:
        return {
            "status": "no_candidates",
            "qa_id": qa_id,
            "question_num_raw_source": question_num_raw_source,
            "trial_num": trial_num,
            "normalized_id": normalized_id,
            "question_type": question_type,
            "question": q,
            "chatbot_answer": a,
            "human_score": human_score,
            "retrieved_context": "",
            "retrieval_meta": [],
        }

    # Deduplicate chunks before reranking
    candidates = deduplicate_chunks(candidates)

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
        "qa_id": qa_id,
        "question_num_raw_source": question_num_raw_source,
        "trial_num": trial_num,
        "normalized_id": normalized_id,
        "question_type": question_type,
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
    emb = slug(args.embedding_model)

    if args.reranker_type == "none":
        rr = "rerank_none"
    elif args.reranker_type == "bge":
        rr = "rerank_bge_" + slug(args.bge_reranker_model)
    elif args.reranker_type == "mxbai":
        rr = "rerank_mxbai_" + slug(args.mxbai_reranker_model)
    else:
        rr = "rerank_qwen_" + slug(args.qwen_reranker_model)

    fname = f"stage1_{args.retrieval_mode}_emb-{emb}_{rr}_finalk-{args.final_k}_qa_query.jsonl"
    out_jsonl = os.path.join(args.out_dir, fname)
    meta_json = out_jsonl + ".meta.json"
    return OutputSpec(out_jsonl=out_jsonl, meta_json=meta_json)

def load_jsonl(input_path: str, test_mode: bool = False, test_n: int = 10) -> List[Dict[str, Any]]:
    records = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                records.append(rec)
                if test_mode and len(records) >= test_n:
                    break
            except json.JSONDecodeError as e:
                print(f"⚠️ Skipping invalid JSON line: {e}")
                continue
    return records

def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--input_jsonl", type=str, default=os.getenv("INPUT_JSONL", "data_eval/test_set0213.jsonl"))
    p.add_argument("--db_folder", type=str, default=os.getenv("DB_FOLDER", "data_preparation/chroma_db_multiscale"))
    p.add_argument("--out_dir", type=str, default=os.getenv("OUT_DIR", "retrieval_cache"))
    p.add_argument("--out_jsonl", type=str, default=os.getenv("OUT_JSONL", ""))

    p.add_argument("--test_mode", action="store_true")
    p.add_argument("--test_n", type=int, default=int(os.getenv("TEST_N", "10")))

    p.add_argument("--force", action="store_true", help="Overwrite output (disables resume).")
    p.add_argument("--max_workers", type=int, default=int(os.getenv("MAX_WORKERS", "6")))

    p.add_argument("--retrieval_mode", choices=["dense_only", "bm25_only", "hybrid_rrf"], default=os.getenv("RETRIEVAL_MODE", "hybrid_rrf"))
    p.add_argument("--dense_k", type=int, default=int(os.getenv("DENSE_K", "10")))
    p.add_argument("--bm25_k", type=int, default=int(os.getenv("BM25_K", "10")))
    p.add_argument("--rrf_k", type=int, default=int(os.getenv("RRF_K", "60")))
    p.add_argument("--final_k", type=int, default=int(os.getenv("FINAL_K", "5")))

    p.add_argument("--embedding_model", type=str, default=os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"))
    p.add_argument("--embedding_device", type=str, default=os.getenv("EMBEDDING_DEVICE", "cpu"))

    p.add_argument("--reranker_type", choices=["none", "qwen", "bge", "mxbai"], default=os.getenv("RERANKER_TYPE", "qwen"))
    p.add_argument("--reranker_device", type=str, default=os.getenv("RERANKER_DEVICE", "cpu"))
    p.add_argument("--reranker_batch_size", type=int, default=int(os.getenv("RERANKER_BATCH_SIZE", "3")))
    p.add_argument("--max_doc_chars_rerank", type=int, default=int(os.getenv("MAX_DOC_CHARS_RERANK", "4000")))

    # Reranker models
    p.add_argument("--qwen_reranker_model", type=str, default=os.getenv("QWEN_RERANKER_MODEL", "Qwen/Qwen3-Reranker-4B"))
    p.add_argument("--bge_reranker_model", type=str, default=os.getenv("BGE_RERANKER_MODEL", "BAAI/bge-reranker-large"))
    p.add_argument("--mxbai_reranker_model", type=str, default=os.getenv("MXBAI_RERANKER_MODEL", "mixedbread-ai/mxbai-rerank-large-v2"))

    return p.parse_args()


def main():
    args = parse_args()

    if _HAS_HF_HUB:
        print(f"🔎 huggingface_hub={huggingface_hub.__version__}")

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
    valid_trial_ids = set()
    if os.path.exists(trial_index_path):
        with open(trial_index_path, "r", encoding="utf-8") as f:
            valid_trial_ids = set(json.load(f))
    else:
        print(f"⚠️  Trial index not found at {trial_index_path}. All trial IDs will be accepted.")

    # Load embeddings + DB
    print(f"⏳ Loading embeddings on {args.embedding_device}: {args.embedding_model}")
    try:
        embedding_fn = HuggingFaceEmbeddings(
            model_name=args.embedding_model,
            model_kwargs={
                "trust_remote_code": True,
                "device": args.embedding_device,
            },
        )
    except Exception as e:
        msg = str(e)
        if ("additional_chat_templates" in msg) or ("RemoteEntryNotFoundError" in msg) or ("404" in msg):
            print("⚠️  HF template/metadata fetch failed; retrying with local_files_only=True (cache-only)...")
            embedding_fn = HuggingFaceEmbeddings(
                model_name=args.embedding_model,
                model_kwargs={
                    "trust_remote_code": True,
                    "device": args.embedding_device,
                    "local_files_only": True,
                },
            )
        else:
            raise
    db = Chroma(persist_directory=args.db_folder, embedding_function=embedding_fn)
    print("✅ Chroma DB loaded")

    # Initialize reranker
    if args.reranker_type == "none":
        reranker = NullReranker()
        reranker_name = "none"
    elif args.reranker_type in ["bge", "mxbai"]:
        if CrossEncoder is None:
            raise RuntimeError("sentence_transformers not installed; cannot use cross-encoder reranker")
        model_name = args.bge_reranker_model if args.reranker_type == "bge" else args.mxbai_reranker_model
        reranker = CrossEncoderReranker(model_name=model_name, device=args.reranker_device)
        reranker_name = model_name
    else:
        reranker = Qwen3Reranker4B(model_name=args.qwen_reranker_model, device=args.reranker_device)
        reranker_name = args.qwen_reranker_model

    # Load data from JSONL
    print(f"📂 Loading JSONL: {args.input_jsonl}")
    all_records = load_jsonl(args.input_jsonl, test_mode=args.test_mode, test_n=args.test_n)
    
    # Worklist (resume-aware)
    work_items: List[Dict[str, Any]] = []
    skipped_count = 0
    for rec in all_records:
        qa_id = rec.get("qa_id")
        if qa_id is None:
            continue
        
        if (not args.force) and (qa_id in existing_keys or str(qa_id) in existing_keys):
            skipped_count += 1
            continue
        
        work_items.append(rec)

    print(f"📌 Total records: {len(all_records)}")
    print(f"📌 Skipped (already processed): {skipped_count}")
    print(f"📌 Rows to process this run: {len(work_items)}")
    print(f"⚙️  retrieval_mode={args.retrieval_mode} | dense_k={args.dense_k} | bm25_k={args.bm25_k} | rrf_k={args.rrf_k} | final_k={args.final_k}")
    print(f"⚙️  embedding={args.embedding_model} | reranker_type={args.reranker_type} | reranker={reranker_name}")
    print(f"📝 Output JSONL: {out_jsonl}")

    bm25_cache: dict = {}

    meta_obj = {
        "input_jsonl": args.input_jsonl,
        "db_folder": args.db_folder,
        "retrieval_mode": args.retrieval_mode,
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

    def write_jsonl(obj: Dict[str, Any]):
        with write_lock:
            with open(out_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # Process
    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = []
        for rec in work_items:
            futures.append(
                ex.submit(
                    process_one,
                    rec,
                    db,
                    reranker,
                    bm25_cache,
                    args.retrieval_mode,
                    args.dense_k,
                    args.bm25_k,
                    args.rrf_k,
                    args.final_k,
                    args.reranker_type,
                    args.reranker_batch_size,
                    args.max_doc_chars_rerank,
                    valid_trial_ids,
                )
            )

        for fut in tqdm(as_completed(futures), total=len(futures), desc="stage1_retrieve"):
            obj = fut.result()
            obj["retrieval_settings"] = meta_obj
            write_jsonl(obj)

    print(f"✅ Stage 1 complete → {out_jsonl}")
    print(f"✅ Metadata saved → {meta_json}")

if __name__ == "__main__":
    main()
