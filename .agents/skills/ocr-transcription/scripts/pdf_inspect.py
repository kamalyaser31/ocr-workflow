"""Pre-flight PDF inspection via the `detect-pdf` CLI from firecrawl/pdf-inspector.

This wrapper performs CLASSIFICATION ONLY — it never invokes `pdf2md` and never
extracts text. Its sole purpose is to populate `output_parts/inspection.json`
with the document's type (`text-based` / `scanned` / `image-based` / `mixed`),
the list of pages needing OCR, RTL detection, and basic layout flags. The main
agent uses that profile to recommend the optimal pipeline (direct PDF extraction
vs. PDF image pipeline) before the user makes their choice.

Install prerequisites (one-time, manual):
    1. Install the Rust toolchain via `winget install Rustlang.Rustup`
       (or download from https://rustup.rs).
    2. Install the CLI: `cargo install pdf-inspector`.

The script refuses to auto-install anything; if the binary is missing it prints
an install hint and exits non-zero, mirroring the pandoc policy.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure local scripts directory is in sys.path for importing _shared
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import (  # noqa: E402
    INSPECTION_FILENAME,
    write_json_atomic,
)

VALID_STRATEGIES = {"early-exit", "full", "sample"}
DEFAULT_STRATEGY = "full"
BINARY_NAME = "detect-pdf"


def find_detect_pdf() -> str | None:
    """Locate the `detect-pdf` binary on PATH or in common cargo install dirs."""
    on_path = shutil.which(BINARY_NAME)
    if on_path:
        return on_path

    # cargo installs binaries to %USERPROFILE%\.cargo\bin on Windows. That
    # location is usually on PATH after the first `rustup` shell, but a fresh
    # agent shell may not see it yet — probe explicitly.
    user_profile = os.environ.get("USERPROFILE", "")
    if user_profile:
        candidate = os.path.join(user_profile, ".cargo", "bin", f"{BINARY_NAME}.exe")
        if os.path.exists(candidate):
            return candidate

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidate = os.path.join(local_app_data, "cargo", "bin", f"{BINARY_NAME}.exe")
        if os.path.exists(candidate):
            return candidate

    return None


def install_hint() -> str:
    """Return the install instructions printed when the binary is missing."""
    return (
        "The 'detect-pdf' CLI from firecrawl/pdf-inspector was not found on PATH.\n"
        "This skill does NOT auto-install system binaries.\n"
        "Install prerequisites (one-time):\n"
        "  1. Install Rust:   winget install Rustlang.Rustup   (or https://rustup.rs)\n"
        "  2. Install the CLI: cargo install pdf-inspector\n"
        "After installation, restart your shell so the new PATH is loaded."
    )


def build_command(binary: str, pdf_path: Path, strategy: str) -> list[str]:
    """Assemble the detect-pdf CLI invocation.

    NOTE: This wrapper ONLY calls `detect-pdf`. The `pdf2md` subcommand is
    hard-banned by the skill's transcription rules and is never reachable from
    this script — there is no code path that constructs or invokes it.
    """
    command = [
        binary,
        str(pdf_path),
        "--analyze",
        "--json",
        "--strategy",
        strategy,
    ]
    return command


def parse_payload(stdout: str, pdf_path: Path) -> dict:
    """Parse detect-pdf's JSON output, attaching source metadata."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"detect-pdf returned non-JSON output for '{pdf_path}': {error}"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"detect-pdf returned unexpected payload type: {type(payload).__name__}"
        )
    payload.setdefault("source_file", str(pdf_path))
    return payload


def run_inspection(
    pdf_path: Path,
    output_dir: Path,
    strategy: str,
    pages_str: str | None,
    echo_json: bool,
) -> dict:
    """Invoke detect-pdf and persist the profile atomically."""
    if strategy not in VALID_STRATEGIES:
        raise ValueError(
            f"Invalid strategy '{strategy}'. Choose one of: "
            f"{', '.join(sorted(VALID_STRATEGIES))}."
        )

    binary = find_detect_pdf()
    if not binary:
        raise FileNotFoundError(install_hint())

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / INSPECTION_FILENAME

    command = build_command(binary, pdf_path, strategy)
    if pages_str:
        # Insert --pages after the positional PDF argument; detect-pdf accepts
        # `--pages 1,3-5` for narrowed inspection.
        command.insert(2, pages_str)
        command.insert(2, "--pages")

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
            f"detect-pdf exited with code {completed.returncode}.\n"
            f"stderr: {completed.stderr.strip() or '(empty)'}"
        )

    profile = parse_payload(completed.stdout, pdf_path)
    if echo_json:
        # Print before writing so callers see the result even if the write
        # fails for an unrelated reason.
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    write_json_atomic(output_path, profile)
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pre-flight PDF inspection (classification only) via firecrawl/"
            "pdf-inspector's `detect-pdf` CLI. Writes output_parts/inspection.json."
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
        choices=sorted(VALID_STRATEGIES),
        default=DEFAULT_STRATEGY,
        help="detect-pdf scan strategy (default: full).",
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