import argparse
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

# Ensure local scripts directory is in sys.path for importing _shared
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import (  # noqa: E402
    write_json_atomic,
    parse_pages,
    make_windows_safe_suffix,
    build_chunk_ranges,
    new_chunk,
)

DEFAULT_DPI = 300


def validate_pdf(input_pdf: Path):
    """Open a PDF and return its document handle and page count."""
    if not input_pdf.is_file():
        raise FileNotFoundError(f"PDF file not found: {input_pdf}")
    try:
        document = fitz.open(str(input_pdf))
        if document.is_encrypted:
            raise ValueError(
                f"The PDF file '{input_pdf}' is encrypted or password-protected."
            )
        page_count = len(document)
    except Exception as error:
        raise RuntimeError(f"Unable to open PDF '{input_pdf}': {error}") from error
    if page_count == 0:
        raise ValueError(f"PDF contains no pages: {input_pdf}")
    return document, page_count


def image_name(source_stem: str, page_number: int) -> str:
    """Return a stable, zero-padded output name."""
    return f"{source_stem}_p{page_number:03d}.png"


def valid_existing_image(path: Path) -> bool:
    """Check that an existing output is a readable nonempty PNG."""
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def render_page(document, page_number: int, output_path: Path, dpi: int) -> None:
    """Render one 1-based PDF page to PNG using PyMuPDF."""
    page = document[page_number - 1]
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix)
    try:
        pix.save(str(output_path))
    finally:
        pix = None  # Release memory buffer explicitly


def get_page_chunk_dir(
    output_dir: Path, page_number: int, chunk_ranges: list[tuple[int, int]]
) -> tuple[int, Path]:
    """Determine chunk part number and directory for a given page number."""
    for part_number, (start_p, end_p) in enumerate(chunk_ranges, start=1):
        if start_p <= page_number <= end_p:
            return part_number, output_dir / f"chunk_{part_number}"
    return 1, output_dir / "chunk_1"


def create_or_load_progress(
    progress_file: Path,
    input_pdf: Path,
    total_pages: int,
    pages: list[int],
    pages_str: str | None,
    pages_per_file: int,
    dpi: int,
) -> dict:
    """Create a unified progress state in output_parts/progress.json or load existing."""
    resolved_pdf = input_pdf.resolve()
    base_name = resolved_pdf.stem
    suffix = make_windows_safe_suffix(pages) if pages_str else ""
    final_filename = f"{base_name}{suffix}.md"
    chunk_ranges = build_chunk_ranges(pages, pages_per_file)

    if progress_file.exists():
        try:
            state = json.loads(progress_file.read_text(encoding="utf-8"))
            if (
                Path(state.get("source_file", "")).resolve() == resolved_pdf
                and state.get("pipeline") == "images"
            ):
                return state
        except (OSError, json.JSONDecodeError):
            pass

    chunks = []
    for part_number, (start_p, end_p) in enumerate(chunk_ranges, start=1):
        first_img = f"chunk_{part_number}/{image_name(base_name, start_p)}"
        chunks.append(new_chunk(part_number, first_img, start_p, end_p))

    progress_data = {
        "source_file": str(resolved_pdf),
        "final_filename": final_filename,
        "pipeline": "images",
        "dpi": dpi,
        "total_pages": total_pages,
        "total_selected_pages": len(pages),
        "is_page_selection": bool(pages_str),
        "is_split": len(chunk_ranges) > 1 or bool(pages_str),
        "chunks": chunks,
    }
    write_json_atomic(progress_file, progress_data)
    return progress_data


def render_images(
    input_pdf: Path,
    output_dir: Path,
    parts_dir: Path,
    pages: list[int],
    pages_str: str | None,
    pages_per_file: int,
    dpi: int,
) -> None:
    """Render all selected pages into PNGs organized by chunk subdirectories."""
    resolved_pdf = input_pdf.resolve()
    resolved_dir = output_dir.resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)

    progress_file = parts_dir / "progress.json"
    document, total_pages = validate_pdf(resolved_pdf)

    try:
        progress_data = create_or_load_progress(
            progress_file,
            resolved_pdf,
            total_pages,
            pages,
            pages_str,
            pages_per_file,
            dpi,
        )
        chunk_ranges = build_chunk_ranges(pages, pages_per_file)

        for idx, page_number in enumerate(pages, start=1):
            part_number, chunk_dir = get_page_chunk_dir(
                resolved_dir, page_number, chunk_ranges
            )
            chunk_dir.mkdir(parents=True, exist_ok=True)

            expected_name = image_name(resolved_pdf.stem, page_number)
            output_path = (chunk_dir / expected_name).resolve()

            if valid_existing_image(output_path):
                continue
            if output_path.exists():
                output_path.unlink()
            render_page(document, page_number, output_path, dpi)
            if not valid_existing_image(output_path):
                raise RuntimeError(f"Rendered image failed validation: {output_path}")
            print(f"[{idx}/{len(pages)}] Created: {output_path}")
    finally:
        document.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render selected PDF pages as independent PNG images using PyMuPDF."
    )
    parser.add_argument("input_pdf", type=Path, help="Source PDF path.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output_images"),
        help="Output directory for images.",
    )
    parser.add_argument(
        "--parts-dir",
        type=Path,
        default=Path("output_parts"),
        help="Output directory for progress tracking (default: output_parts).",
    )
    parser.add_argument(
        "--pages",
        help="1-based pages, for example: 1-3,8. Defaults to all pages.",
    )
    parser.add_argument(
        "--pages_per_file",
        type=int,
        default=20,
        help="Pages per chunk (default: 20).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help="Rendering resolution (default: 300).",
    )
    parser.add_argument(
        "--info-only", action="store_true", help="Print page count and exit."
    )
    args = parser.parse_args()

    try:
        resolved_pdf = args.input_pdf.resolve()
        resolved_dir = args.output_dir.resolve()
        parts_dir = args.parts_dir.resolve()

        document, total_pages = validate_pdf(resolved_pdf)
        document.close()

        pages = parse_pages(args.pages, total_pages)

        if args.info_only:
            print(f"Total pages: {total_pages}")
            print(f"Selected pages: {len(pages)}")
            return 0

        if not pages:
            raise ValueError("Page selection contains no valid pages.")

        if args.dpi < 1:
            raise ValueError("DPI must be at least 1.")

        render_images(
            resolved_pdf,
            resolved_dir,
            parts_dir,
            pages,
            args.pages,
            args.pages_per_file,
            args.dpi,
        )
        print(f"Completed: {len(pages)} image(s) in {resolved_dir}")
        print(f"Workflow tracking file initialized at: {parts_dir / 'progress.json'}")
        return 0
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
