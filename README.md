# LLM-as-Judge (LAJ) Evaluation Pipeline

## Evaluation Criteria

1. Critical harms risks
2. Individualized medical advice
3. Inappropriate persuasion
4. Transparency
5. Factual accuracy 
6. Usefulness
7. Clarity and succinctness

## NoRAG Evaluation

**Scales**: criteria 1-3 are binary, criteria 4-7 are Likert scale (1-5).

**Models**: 
- GPT-5
- Qwen3-30B
- MedGemma-27B
- Qwen-4B

**Prompt Variations**:
- Baseline
- Definition-only (`def_only`)
- Chain-of-thought (`def_cot`)
- w/ and w/o demonstrations 

## RAG Evaluation (for criterion 4 factual accuracy)

**Pipeline**: 2 stages

**Stage 1 - Retrieval**:
- Scales: `short` (document chunk scale, 2000 characters max)
- Retrieval methods: Dense-only, BM25-only, Hybrid RRF
- Embedding models: BAAI/bge-m3, Qwen/Qwen3-Embedding-8B
- Rerankers: BAAI/bge-reranker-v2-m3, Qwen/Qwen3-Reranker-4B, or none
- Final k: 3 chunks

**Stage 2 - LAJ Scoring**:
- Models: Qwen3-30B, MedGemma-27B
- Prompt variations: Baseline, Definition-only, Chain-of-thought
- Demonstrations: w/ and w/o demonstrations 
- Uses retrieved context from Stage 1
- Oracle: GPT-5