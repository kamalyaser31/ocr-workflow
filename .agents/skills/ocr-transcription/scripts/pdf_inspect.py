"""Pre-flight PDF inspection via the `pdf-inspector` CLI from firecrawl (npm).

This wrapper performs CLASSIFICATION ONLY — it never invokes text extraction.
Its sole purpose is to populate `output_parts/inspection.json` with the
document's type (`text-based` / `scanned` / `image-based` / `mixed`), the list
of pages needing OCR, RTL detection, and basic layout flags. The main agent uses
that profile to recommend the optimal pipeline before the user makes their choice.

Install prerequisites (one-time, manual):
    npm install -g @firecrawl/pdf-inspector

The script refuses to auto-install anything; if the binary is missing it prints
an install hint and exits non-zero, mirroring the pandoc policy.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import (  # noqa: E402
    INSPECTION_FILENAME,
    write_json_atomic,
)

BINARY_NAME = "pdf-inspector"
SUBCOMMAND = "detect"


def find_pdf_inspector() -> str | None:
    """Locate the `pdf-inspector` binary on PATH."""
    return shutil.which(BINARY_NAME)


def install_hint() -> str:
    """Return the install instructions printed when the binary is missing."""
    return (
        "The 'pdf-inspector' CLI from @firecrawl/pdf-inspector was not found on PATH.\n"
        "This skill does NOT auto-install system binaries.\n"
        "Install prerequisites (one-time):\n"
        "  npm install -g @firecrawl/pdf-inspector\n"
        "After installation, restart your shell so the new PATH is loaded."
    )


def build_command(binary: str, pdf_path: Path) -> list[str]:
    """Assemble the pdf-inspector CLI invocation (classification only)."""
    return [binary, SUBCOMMAND, str(pdf_path), "--json"]


def _normalize_profile(raw: dict, pdf_path: Path) -> dict:
    """Normalize npm pdf-inspector camelCase output to snake_case schema."""
    pdf_type_raw = raw.get("pdfType", "unknown")
    # Map npm type names to the canonical skill schema values
    type_map = {
        "TextBased": "text-based",
        "Scanned": "scanned",
        "ImageBased": "image-based",
        "Mixed": "mixed",
    }
    pdf_type = type_map.get(pdf_type_raw, pdf_type_raw.lower())

    pages_needing_ocr = raw.get("pagesNeedingOcr", [])

    return {
        "pdf_type": pdf_type,
        "confidence": raw.get("confidence"),
        "total_pages": raw.get("pageCount"),
        "pages_needing_ocr": pages_needing_ocr,
        "is_rtl": raw.get("isRtl", False),
        "has_tables": raw.get("hasTables", False),
        "has_multi_column": raw.get("hasMultiColumn", False),
        "source_file": str(pdf_path),
    }


def parse_payload(stdout: str, pdf_path: Path) -> dict:
    """Parse pdf-inspector's JSON output and normalize to the skill schema."""
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"pdf-inspector returned non-JSON output for '{pdf_path}': {error}"
        ) from error
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"pdf-inspector returned unexpected payload type: {type(raw).__name__}"
        )
    return _normalize_profile(raw, pdf_path)


def run_inspection(
    pdf_path: Path,
    output_dir: Path,
    strategy: str,
    pages_str: str | None,
    echo_json: bool,
) -> dict:
    """Invoke pdf-inspector detect and persist the profile atomically."""
    # strategy parameter is kept for API compatibility with callers/tests
    # but pdf-inspector detect has no --strategy flag.
    _ = strategy

    binary = find_pdf_inspector()
    if not binary:
        raise FileNotFoundError(install_hint())

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / INSPECTION_FILENAME

    command = build_command(binary, pdf_path)
    if pages_str:
        command += ["--pages", pages_str]

    print(f"Executing: {' '.join(command)}")
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise RuntimeError(
            f"Failed to execute '{binary}': {error}\n{install_hint()}"
        ) from error

    if completed.returncode != 0:
        raise RuntimeError(
            f"pdf-inspector exited with code {completed.returncode}.\n"
            f"stderr: {completed.stderr.strip() or '(empty)'}"
        )

    profile = parse_payload(completed.stdout, pdf_path)
    if echo_json:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    write_json_atomic(output_path, profile)
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pre-flight PDF inspection (classification only) via "
            "@firecrawl/pdf-inspector. Writes output_parts/inspection.json."
        )
    )
    parser.add_argument("input_pdf", type=Path, help="Source PDF path.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output_parts"),
        help="Directory where inspection.json is written (default: output_parts).",
    )
    parser.add_argument(
        "--pages",
        help="1-based pages/ranges to narrow inspection, e.g., '1-3,8'.",
    )
    parser.add_argument(
        "--strategy",
        choices=["early-exit", "full", "sample"],
        default="full",
        help="Ignored (kept for API compatibility). pdf-inspector detect has no strategy flag.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="echo_json",
        help="Echo the parsed profile as JSON to stdout.",
    )
    args = parser.parse_args()

    try:
        if not args.input_pdf.is_file():
            raise FileNotFoundError(f"PDF file not found: {args.input_pdf}")
        profile = run_inspection(
            args.input_pdf,
            args.output_dir,
            args.strategy,
            args.pages,
            args.echo_json,
        )
        pdf_type = profile.get("pdf_type", "unknown")
        confidence = profile.get("confidence")
        confidence_text = (
            f", confidence={confidence:.2f}" if isinstance(confidence, (int, float)) else ""
        )
        print(
            f"Inspection complete: pdf_type={pdf_type}{confidence_text}. "
            f"Profile written to: {args.output_dir / INSPECTION_FILENAME}"
        )
        return 0
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())