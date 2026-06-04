import argparse
import json
import os
import re
import shutil
from pathlib import Path

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-8B"
OPTIMAL_CHUNK_SIZE = 2000
OPTIMAL_OVERLAP = 250


def extract_nct_id_from_text(text, filename):
    match = re.search(r"(NCT\d+)", text)
    if match:
        return match.group(1)

    match = re.search(r"(NCT\d+)", filename)
    if match:
        return match.group(1)

    return None


def apply_semantic_chunking(text, base_metadata):
    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]
    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,
    )
    character_splitter = RecursiveCharacterTextSplitter(
        chunk_size=OPTIMAL_CHUNK_SIZE,
        chunk_overlap=OPTIMAL_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    md_chunks = markdown_splitter.split_text(text)
    for chunk in md_chunks:
        chunk.metadata = {**base_metadata, **chunk.metadata}

    final_chunks = character_splitter.split_documents(md_chunks)
    for idx, chunk in enumerate(final_chunks):
        chunk.metadata["chunk_index"] = idx

    return final_chunks


def apply_semantic_tagging(chunk):
    text = chunk.page_content.lower()
    headers_text = " ".join(
        str(chunk.metadata.get(f"Header {i}", "")).lower()
        for i in range(1, 4)
    )
    combined_text = text + " " + headers_text

    semantic_dict = [
        ("purpose", ["purpose", "objective", "goal", "aim", "why this study"]),
        ("procedures", ["procedure", "protocol", "what will happen"]),
        ("risks", ["risk", "side effect", "adverse", "harm"]),
        ("benefits", ["benefit", "may help", "advantage"]),
        ("alternatives", ["alternative", "other option"]),
        ("confidentiality", ["confidential", "privacy", "protect"]),
        ("injury_compensation", ["injury", "compensation", "hurt"]),
        ("voluntariness_withdrawal", ["withdraw", "leave", "voluntary"]),
        ("contact_information", ["contact", "phone", "email", "questions"]),
        ("eligibility", ["eligible", "inclusion", "exclusion", "take part"]),
        ("unknown_risks", ["unknown risk", "unforeseeable"]),
        ("termination", ["terminate", "end study", "take you off"]),
        ("new_findings", ["new finding", "new information"]),
        ("costs", ["cost", "expense", "financial", "pay"]),
        ("future_use", ["future use", "stored", "biobank"]),
        ("payment", ["payment", "paid", "reimbursed"]),
    ]

    for tag, keywords in semantic_dict:
        if any(keyword in combined_text for keyword in keywords):
            chunk.metadata["semantic_tag"] = tag
            return chunk

    chunk.metadata["semantic_tag"] = "general"
    return chunk


def build_optimized_vector_db(input_folder, output_db, embedding_model_name=EMBEDDING_MODEL_NAME):
    input_folder = Path(input_folder).resolve()
    output_db = Path(output_db).resolve()
    trial_index_path = output_db / "available_trials.json"

    if not input_folder.exists():
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    if output_db.exists():
        print(f"Removing existing DB at {output_db}")
        shutil.rmtree(output_db)

    output_db.mkdir(parents=True, exist_ok=True)
    print("Building optimized two-stage Chroma DB (2000 chars)")

    embedding_device = os.getenv("EMBEDDING_DEVICE", "cpu")
    model_kwargs = {"trust_remote_code": True}
    if embedding_device and embedding_device != "cpu":
        model_kwargs["device"] = embedding_device
    embedding_model = HuggingFaceEmbeddings(
        model_name=embedding_model_name,
        model_kwargs=model_kwargs,
    )

    all_chunks = []
    all_trial_ids = set()
    files_processed = 0

    for path in sorted(input_folder.rglob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
            nct_id = extract_nct_id_from_text(text, path.name)
            if not nct_id:
                continue

            all_trial_ids.add(nct_id)
            base_metadata = {
                "nct_id": nct_id,
                "filename": path.name,
                "source_folder": path.parent.name,
            }
            chunks = apply_semantic_chunking(text, base_metadata)
            for chunk in chunks:
                apply_semantic_tagging(chunk)
            all_chunks.extend(chunks)
            files_processed += 1
            print(f"{path.name}: {len(chunks)} optimized chunks")
        except Exception as exc:
            print(f"Error processing {path.name}: {exc}")

    if not all_chunks:
        raise RuntimeError("No chunks generated. Aborting.")

    print(f"\nSaving {len(all_chunks)} chunks to Chroma DB")
    Chroma.from_documents(
        documents=all_chunks,
        embedding=embedding_model,
        persist_directory=str(output_db),
    )

    trial_index_path.write_text(json.dumps(sorted(all_trial_ids), indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("DATABASE BUILD COMPLETE")
    print(f"Trials indexed: {len(all_trial_ids)}")
    print(f"Files processed: {files_processed}")
    print(f"Total chunks generated: {len(all_chunks)}")
    print(f"Vector DB saved to: {output_db}")
    print(f"Trial index saved to: {trial_index_path}")
    print("=" * 60)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_folder", required=True, help="Directory containing cleaned Markdown-formatted .txt files")
    parser.add_argument("--output_db", required=True, help="Directory where the Chroma DB will be created")
    parser.add_argument("--embedding_model", default=EMBEDDING_MODEL_NAME)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_optimized_vector_db(
        input_folder=args.input_folder,
        output_db=args.output_db,
        embedding_model_name=args.embedding_model,
    )
