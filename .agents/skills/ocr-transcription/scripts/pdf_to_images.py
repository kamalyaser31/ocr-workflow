import argparse
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

# Ensure local scripts directory is in sys.path for importing _shared
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import write_json_atomic, parse_pages  # noqa: E402

DEFAULT_DPI = 300
STATE_FILENAME = ".progress.json"


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


def prepare_output_dir(output_dir: Path) -> Path:
    """Create a workspace or refuse unrelated existing files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / STATE_FILENAME
    if not any(output_dir.iterdir()):
        return state_path
    if not state_path.is_file():
        raise RuntimeError(
            f"Output directory is not empty and has no {STATE_FILENAME}: "
            f"{output_dir}"
        )
    return state_path


def make_state(input_pdf: Path, pages: list[int], dpi: int) -> dict:
    """Create a resumable state document."""
    resolved_pdf = input_pdf.resolve()
    return {
        "source_file": str(resolved_pdf),
        "source_name": resolved_pdf.stem,
        "dpi": dpi,
        "pages": {
            str(page): {
                "status": "pending",
                "output": image_name(resolved_pdf.stem, page),
            }
            for page in pages
        },
    }


def load_or_create_state(
    state_path: Path, input_pdf: Path, pages: list[int], dpi: int
) -> dict:
    """Load a compatible state or create a new one."""
    resolved_pdf = input_pdf.resolve()
    if not state_path.exists():
        state = make_state(resolved_pdf, pages, dpi)
        write_json_atomic(state_path, state)
        return state

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid progress file '{state_path}': {error}") from error

    if (
        Path(state.get("source_file", "")).resolve() != resolved_pdf
        or state.get("dpi") != dpi
        or sorted(int(page) for page in state.get("pages", {})) != pages
    ):
        raise RuntimeError(
            "Existing progress state belongs to a different PDF, DPI, or "
            "page selection."
        )
    return state


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


def render_images(
    input_pdf: Path, output_dir: Path, pages: list[int], dpi: int
) -> None:
    """Render all pending pages and update state after each successful page."""
    resolved_pdf = input_pdf.resolve()
    resolved_dir = output_dir.resolve()
    state_path = prepare_output_dir(resolved_dir)
    state = load_or_create_state(state_path, resolved_pdf, pages, dpi)
    document, _ = validate_pdf(resolved_pdf)
    try:
        for idx, page_number in enumerate(pages, start=1):
            record = state["pages"].get(str(page_number))
            if not record:
                raise RuntimeError(f"Page {page_number} not found in progress state.")

            status = record.get("status")
            if status not in ("pending", "completed"):
                raise ValueError(
                    f"Invalid status '{status}' for page {page_number} in state."
                )

            output_value = record.get("output")
            if output_value:
                resolved_output = (resolved_dir / output_value).resolve()
                try:
                    resolved_output.relative_to(resolved_dir)
                except ValueError:
                    raise ValueError(
                        f"Target path traversal detected in state file: {output_value}"
                    )

            expected_name = image_name(resolved_pdf.stem, page_number)
            output_path = (resolved_dir / expected_name).resolve()
            try:
                output_path.relative_to(resolved_dir)
            except ValueError:
                raise ValueError(
                    f"Target path traversal detected for page "
                    f"{page_number}: {output_path}"
                )

            record["output"] = expected_name

            if status == "completed" and valid_existing_image(output_path):
                continue
            if output_path.exists() and not valid_existing_image(output_path):
                output_path.unlink()
            render_page(document, page_number, output_path, dpi)
            if not valid_existing_image(output_path):
                raise RuntimeError(f"Rendered image failed validation: {output_path}")
            record["status"] = "completed"
            write_json_atomic(state_path, state)
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
        help="Output directory.",
    )
    parser.add_argument(
        "--pages",
        help="1-based pages, for example: 1-3,8. Defaults to all pages.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help="Rendering resolution (default: 150).",
    )
    parser.add_argument(
        "--info-only", action="store_true", help="Print page count and exit."
    )
    args = parser.parse_args()

    try:
        resolved_pdf = args.input_pdf.resolve()
        resolved_dir = args.output_dir.resolve()

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

        render_images(resolved_pdf, resolved_dir, pages, args.dpi)
        print(f"Completed: {len(pages)} image(s) in {resolved_dir}")
        return 0
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
