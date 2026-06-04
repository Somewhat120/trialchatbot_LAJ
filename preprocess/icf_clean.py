import argparse
import re
from collections import Counter
from pathlib import Path


def clean_icf_by_frequency(markdown_text, min_repeats=5, max_length=120):
    """Remove recurring structural noise (headers, footers, stamps) using frequency analysis."""
    lines = markdown_text.split("\n")

    def normalize_line(text):
        normalized = text.lower().strip()
        normalized = re.sub(r"^#+\s*", "", normalized)
        normalized = re.sub(r"\d+", "[NUM]", normalized)
        return normalized

    line_counts = Counter()
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) <= max_length:
            normalized = normalize_line(stripped)
            if normalized:
                line_counts[normalized] += 1

    noise_signatures = {
        normalized
        for normalized, count in line_counts.items()
        if count >= min_repeats
    }

    cleaned_lines = []
    dropped_count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) > max_length:
            cleaned_lines.append(line)
            continue

        normalized = normalize_line(stripped)
        if normalized in noise_signatures:
            dropped_count += 1
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines), dropped_count


def clean_dataset(input_dir, output_dir, min_repeats=5):
    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()

    if not input_dir.exists():
        print(f"ERROR: Input directory does not exist: {input_dir}")
        return

    print(f"Reading raw text from: {input_dir}")
    processed_count = 0

    for input_filepath in sorted(input_dir.rglob("*.txt")):
        relative_path = input_filepath.relative_to(input_dir)
        output_filepath = output_dir / relative_path
        output_filepath.parent.mkdir(parents=True, exist_ok=True)

        print(f"Processing: {relative_path}")
        try:
            raw_text = input_filepath.read_text(encoding="utf-8")
            clean_text, dropped = clean_icf_by_frequency(raw_text, min_repeats=min_repeats)
            output_filepath.write_text(clean_text, encoding="utf-8")
            print(f"  -> Saved! (Dropped {dropped} lines of noise)")
            processed_count += 1
        except Exception as exc:
            print(f"  -> Error processing {input_filepath.name}: {exc}")

    print(f"\n{'=' * 60}")
    print(f"Summary: Cleaned {processed_count} files.")
    print(f"Cleaned dataset saved to: {output_dir}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", required=True, help="Directory containing Markdown-formatted .txt files from Docling conversion")
    parser.add_argument("--output_dir", required=True, help="Directory where cleaned .txt files will be written")
    parser.add_argument("--min_repeats", type=int, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    clean_dataset(args.input_dir, args.output_dir, min_repeats=args.min_repeats)
