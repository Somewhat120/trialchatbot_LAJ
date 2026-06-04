import argparse
import csv
from pathlib import Path

from docling.document_converter import DocumentConverter

ICF_MARKER = "ICF_"
SHOW_TOKEN_STATS = False

converter = DocumentConverter()


def count_tokens(text):
    return len(text.split())


def convert_pdf_to_text_docling(pdf_path, txt_path, nct_number, stats_writer=None):
    """Extract text from a PDF as Markdown using Docling."""
    try:
        result = converter.convert(str(pdf_path))
        md_text = result.document.export_to_markdown()

        txt_path.parent.mkdir(parents=True, exist_ok=True)
        with txt_path.open("w", encoding="utf-8") as handle:
            handle.write(md_text)

        print(f"Converted: {pdf_path.name} -> {txt_path.name}")

        total_tokens = count_tokens(md_text)
        if SHOW_TOKEN_STATS:
            print(f"  TOTAL tokens: {total_tokens}")

        if stats_writer is not None:
            stats_writer.writerow({
                "NCT_Number": nct_number,
                "PDF_File": pdf_path.name,
                "Txt_File": txt_path.name,
                "Num_Pages": "N/A",
                "Total_Tokens": total_tokens,
                "Tokens_Per_Page": "N/A",
            })
    except Exception as exc:
        print(f"Error converting {pdf_path}: {exc}")
        import traceback

        traceback.print_exc()


def _icf_number_from_filename(filename):
    if ICF_MARKER not in filename or not filename.lower().endswith(".pdf"):
        return None
    base = Path(filename).stem
    idx = base.index(ICF_MARKER) + len(ICF_MARKER)
    return base[idx:] or "0"


def process_all_prot_pdfs(root_dir, output_dir):
    root_dir = Path(root_dir).resolve()
    output_dir = Path(output_dir).resolve()

    if not root_dir.exists():
        print(f"ERROR: Root directory does not exist: {root_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing PDFs from: {root_dir}")
    print(f"Looking for PDFs containing: {ICF_MARKER}")
    print(f"Output directory: {output_dir}")

    pdf_count = 0
    processed_count = 0
    fieldnames = ["NCT_Number", "PDF_File", "Txt_File", "Num_Pages", "Total_Tokens", "Tokens_Per_Page"]

    for category_path in sorted(root_dir.iterdir()):
        if not category_path.is_dir():
            continue
        if category_path.name == output_dir.name:
            print(f"Skipping output directory: {category_path.name}")
            continue

        print(f"\nChecking category: {category_path.name}")
        category_out = output_dir / category_path.name
        category_out.mkdir(parents=True, exist_ok=True)
        stats_csv = category_out / "token_stats.csv"

        with stats_csv.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for trial_path in sorted(category_path.iterdir()):
                if not trial_path.is_dir():
                    continue

                for pdf_path in sorted(trial_path.glob(f"*{ICF_MARKER}*.pdf")):
                    pdf_count += 1
                    icf_number = _icf_number_from_filename(pdf_path.name)
                    txt_filename = f"{trial_path.name}_{icf_number}.txt"
                    txt_path = category_out / txt_filename
                    print(f"  {trial_path.name}: {pdf_path.name} -> {category_path.name}/{txt_filename}")
                    convert_pdf_to_text_docling(pdf_path, txt_path, trial_path.name, writer)
                    processed_count += 1

    print(f"\n{'=' * 60}")
    print(f"Summary: Found {pdf_count} PDFs, processed {processed_count} files")
    print(f"Output saved to: {output_dir} (token_stats.csv under each cancer type)")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", required=True, help="Directory containing ICF PDFs organized by cancer type and NCT ID")
    parser.add_argument("--output_dir", required=True, help="Directory where Markdown-formatted .txt outputs will be written")
    parser.add_argument("--test_single", action="store_true")
    parser.add_argument("--test_category", default="cervical_cancer")
    parser.add_argument("--test_nct", default="NCT01554397")
    parser.add_argument("--test_pdf_name", default="ICF_001.pdf")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    fieldnames = ["NCT_Number", "PDF_File", "Txt_File", "Num_Pages", "Total_Tokens", "Tokens_Per_Page"]
    if args.test_single:
        test_pdf = input_dir / args.test_category / args.test_nct / args.test_pdf_name
        test_output_dir = output_dir / args.test_category
        test_output_dir.mkdir(parents=True, exist_ok=True)
        icf_number = _icf_number_from_filename(args.test_pdf_name) or "001"
        test_txt = test_output_dir / f"{args.test_nct}_{icf_number}.txt"
        print("Testing single file conversion...")
        print(f"PDF: {test_pdf}")
        print(f"Output: {test_txt}")
        if not test_pdf.exists():
            print(f"ERROR: Test PDF not found at {test_pdf}")
        else:
            test_stats_csv = test_output_dir / "token_stats.csv"
            with test_stats_csv.open("w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                convert_pdf_to_text_docling(test_pdf, test_txt, args.test_nct, writer)
    else:
        process_all_prot_pdfs(input_dir, output_dir)
