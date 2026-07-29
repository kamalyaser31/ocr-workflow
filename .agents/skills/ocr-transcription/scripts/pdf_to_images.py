import argparse
import json
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ImageRenderRequest:
    input_pdf: Path
    output_dir: Path
    parts_dir: Path
    pages: tuple[int, ...]
    pages_str: str | None = None
    pages_per_file: int = 20
    dpi: int = DEFAULT_DPI


def validate_pdf(input_pdf: Path):
    """Open a PDF and return its document handle and page count."""
    if not input_pdf.is_file():
        raise FileNotFoundError(f"PDF file not found: {input_pdf}")
    try:
        document = fitz.open(str(input_pdf))
    except (fitz.EmptyFileError, fitz.FileDataError, RuntimeError) as error:
        raise RuntimeError(f"Unable to open PDF '{input_pdf}': {error}") from error
    if document.is_encrypted:
        document.close()
        raise ValueError(
            f"The PDF file '{input_pdf}' is encrypted or password-protected."
        )
    page_count = len(document)
    if page_count == 0:
        document.close()
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
    raise ValueError(f"Page {page_number} is outside the requested chunk ranges.")


def image_chunk_ranges(request: ImageRenderRequest) -> list[tuple[int, int]]:
    """Return the chunk ranges required by one image request."""
    return build_chunk_ranges(list(request.pages), request.pages_per_file)


def image_chunk_matches(state_chunk: object, expected_chunk: dict) -> bool:
    """Check the durable fields required by downstream OCR stages."""
    if not isinstance(state_chunk, dict):
        return False
    durable_fields = ("part", "filename", "start_page", "end_page")
    status = state_chunk.get("status")
    output_file = state_chunk.get("output_file")
    return (
        all(state_chunk.get(key) == expected_chunk[key] for key in durable_fields)
        and status in {"pending", "completed", "failed"}
        and isinstance(output_file, str)
        and (status != "completed" or bool(output_file))
    )


def load_image_progress(
    progress_file: Path,
    request: ImageRenderRequest,
    total_pages: int,
) -> dict:
    """Load only a state that exactly matches the requested image run."""
    try:
        state = json.loads(progress_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Existing progress file is unreadable: {progress_file}"
        ) from error
    if not isinstance(state, dict):
        raise RuntimeError(f"Existing progress file is invalid: {progress_file}")

    expected_state = new_image_progress(request, total_pages)
    invariant_fields = (
        "final_filename",
        "pipeline",
        "dpi",
        "total_pages",
        "total_selected_pages",
        "is_page_selection",
        "is_split",
    )
    try:
        source_matches = (
            Path(state.get("source_file", "")).resolve() == request.input_pdf
        )
    except (OSError, TypeError, ValueError):
        source_matches = False
    state_chunks = state.get("chunks")
    expected_chunks = expected_state["chunks"]
    matches_request = (
        source_matches
        and all(state.get(key) == expected_state[key] for key in invariant_fields)
        and isinstance(state_chunks, list)
        and len(state_chunks) == len(expected_chunks)
        and all(
            image_chunk_matches(state_chunk, expected_chunk)
            for state_chunk, expected_chunk in zip(state_chunks, expected_chunks)
        )
    )
    if not matches_request:
        raise RuntimeError(
            f"Existing progress file belongs to another run: {progress_file}"
        )
    return state


def new_image_progress(request: ImageRenderRequest, total_pages: int) -> dict:
    """Build the initial state for an image rendering run."""
    suffix = make_windows_safe_suffix(list(request.pages)) if request.pages_str else ""
    final_filename = f"{request.input_pdf.stem}{suffix}.md"
    chunks = []
    for part_number, (start_p, end_p) in enumerate(
        image_chunk_ranges(request), start=1
    ):
        first_img = (
            f"chunk_{part_number}/" f"{image_name(request.input_pdf.stem, start_p)}"
        )
        chunks.append(new_chunk(part_number, first_img, start_p, end_p))
    return {
        "source_file": str(request.input_pdf),
        "final_filename": final_filename,
        "pipeline": "images",
        "dpi": request.dpi,
        "total_pages": total_pages,
        "total_selected_pages": len(request.pages),
        "is_page_selection": bool(request.pages_str),
        "is_split": len(chunks) > 1 or bool(request.pages_str),
        "chunks": chunks,
    }


def create_or_load_progress(
    progress_file: Path,
    request: ImageRenderRequest,
    total_pages: int,
) -> dict:
    """Create image progress or load the exact matching run."""
    if progress_file.exists():
        return load_image_progress(progress_file, request, total_pages)
    progress_data = new_image_progress(request, total_pages)
    write_json_atomic(progress_file, progress_data)
    return progress_data


def prepare_image_workspace(request: ImageRenderRequest) -> None:
    """Create a new image workspace or preserve a tracked resumable run."""
    progress_file = request.parts_dir / "progress.json"
    if not progress_file.exists():
        for directory in (request.parts_dir, request.output_dir):
            if directory.exists() and any(directory.iterdir()):
                raise RuntimeError(
                    f"Output directory '{directory}' is not empty. "
                    "Preserve or resume that run first."
                )
    request.output_dir.mkdir(parents=True, exist_ok=True)
    request.parts_dir.mkdir(parents=True, exist_ok=True)


def validate_render_request(request: ImageRenderRequest) -> None:
    """Reject image requests that cannot produce a bounded run."""
    if not request.pages:
        raise ValueError("Page selection contains no valid pages.")
    if request.pages_per_file < 1:
        raise ValueError("pages_per_file must be at least 1.")
    if request.dpi < 1:
        raise ValueError("DPI must be at least 1.")


def render_images(request: ImageRenderRequest) -> None:
    """Render all selected pages into PNGs organized by chunk subdirectories."""
    request = ImageRenderRequest(
        input_pdf=request.input_pdf.resolve(),
        output_dir=request.output_dir.resolve(),
        parts_dir=request.parts_dir.resolve(),
        pages=request.pages,
        pages_str=request.pages_str,
        pages_per_file=request.pages_per_file,
        dpi=request.dpi,
    )
    validate_render_request(request)
    prepare_image_workspace(request)
    progress_file = request.parts_dir / "progress.json"
    document, total_pages = validate_pdf(request.input_pdf)

    try:
        create_or_load_progress(
            progress_file,
            request,
            total_pages,
        )
        chunk_ranges = image_chunk_ranges(request)

        for idx, page_number in enumerate(request.pages, start=1):
            part_number, chunk_dir = get_page_chunk_dir(
                request.output_dir, page_number, chunk_ranges
            )
            chunk_dir.mkdir(parents=True, exist_ok=True)

            expected_name = image_name(request.input_pdf.stem, page_number)
            output_path = (chunk_dir / expected_name).resolve()

            if valid_existing_image(output_path):
                continue
            if output_path.exists():
                output_path.unlink()
            render_page(document, page_number, output_path, request.dpi)
            if not valid_existing_image(output_path):
                raise RuntimeError(f"Rendered image failed validation: {output_path}")
            print(f"[{idx}/{len(request.pages)}] Created: {output_path}")
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

        if args.pages_per_file < 1:
            raise ValueError("pages_per_file must be at least 1.")

        if args.dpi < 1:
            raise ValueError("DPI must be at least 1.")

        render_request = ImageRenderRequest(
            input_pdf=resolved_pdf,
            output_dir=resolved_dir,
            parts_dir=parts_dir,
            pages=tuple(pages),
            pages_str=args.pages,
            pages_per_file=args.pages_per_file,
            dpi=args.dpi,
        )
        render_images(render_request)
        print(f"Completed: {len(pages)} image(s) in {resolved_dir}")
        print(f"Workflow tracking file initialized at: {parts_dir / 'progress.json'}")
        return 0
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
