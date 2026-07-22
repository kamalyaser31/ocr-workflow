import os
import argparse
import sys
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

# Ensure local scripts directory is in sys.path for importing _shared
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import (
    write_json_atomic,
    parse_pages,
    group_contiguous,
    make_windows_safe_suffix,
    build_chunk_ranges,
    new_chunk,
)  # noqa: E402


def prepare_output_dir(output_dir: str) -> None:
    """Create an empty workspace or refuse to overwrite an earlier run."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        return
    if any(os.scandir(output_dir)):
        raise RuntimeError(
            f"Output directory '{output_dir}' is not empty. Preserve or "
            "resume that run first."
        )


def safe_load_pdf(input_pdf_path: str):
    """Load a nonempty, unencrypted PDF."""
    if not os.path.exists(input_pdf_path):
        raise FileNotFoundError(f"PDF file not found at '{input_pdf_path}'")
    try:
        reader = PdfReader(input_pdf_path)
        if reader.is_encrypted:
            raise ValueError(
                f"The PDF file '{input_pdf_path}' is encrypted or "
                "password-protected."
            )
        total_pages = len(reader.pages)
        if total_pages == 0:
            raise ValueError(
                f"The PDF file '{input_pdf_path}' contains 0 pages or is " "corrupted."
            )
        return reader, total_pages
    except (PdfReadError, OSError) as e:
        raise RuntimeError(
            f"Failed to read PDF file '{input_pdf_path}'. It may be corrupt "
            f"or invalid. Detail: {e}"
        ) from e


def get_pdf_info(input_pdf_path: str):
    """Returns the total number of pages in the PDF."""
    _, total_pages = safe_load_pdf(input_pdf_path)
    return total_pages





def write_chunk_pdf(reader, page_range, output_path: str) -> None:
    """Write one selected PDF range."""
    start_page, end_page = page_range
    writer = PdfWriter()
    for page_index in range(start_page - 1, end_page):
        writer.add_page(reader.pages[page_index])
    with open(output_path, "wb") as output_file:
        writer.write(output_file)


def initialize_chunks_state(reader, progress_data, chunk_ranges) -> None:
    """Populate chunk records in progress_data without writing files yet."""
    input_pdf_path = progress_data["source_file"]
    if len(chunk_ranges) == 1 and not progress_data["is_page_selection"]:
        progress_data["chunks"].append(
            new_chunk(1, os.path.basename(input_pdf_path), 1, len(reader.pages))
        )
        return
    base_name = os.path.splitext(os.path.basename(input_pdf_path))[0]
    for part_number, page_range in enumerate(chunk_ranges, start=1):
        start_page, end_page = page_range
        filename = f"{base_name}_part_{part_number}_p{start_page}-{end_page}.pdf"
        progress_data["chunks"].append(
            new_chunk(part_number, filename, start_page, end_page)
        )


def write_chunk_pdfs_from_state(reader, output_dir, progress_data) -> None:
    """Write the chunk PDF files for chunks in the progress state."""
    if len(progress_data["chunks"]) == 1 and not progress_data["is_page_selection"]:
        print("Single file workflow initialized (no splitting).")
        return
    print(f"Number of output chunk files: {len(progress_data['chunks'])}")
    for chunk in progress_data["chunks"]:
        filename = chunk["filename"]
        start_page = chunk["start_page"]
        end_page = chunk["end_page"]
        write_chunk_pdf(
            reader, (start_page, end_page), os.path.join(output_dir, filename)
        )
        print(f"Created: {filename} (Pages: {start_page} to {end_page})")


def build_progress_data(
    input_pdf_path, final_filename, total_pages, selected_pages
) -> dict:
    """Create workflow state before chunk records are appended."""
    return {
        "source_file": input_pdf_path,
        "final_filename": final_filename,
        "pipeline": "pdf",
        "total_pages": total_pages,
        "total_selected_pages": len(selected_pages),
        "is_page_selection": False,
        "is_split": False,
        "chunks": [],
    }


def prepare_split_state(input_pdf_path, pages_str, pages_per_file):
    """Validate selection and build the initial workflow state."""
    if pages_per_file < 1:
        raise ValueError("pages_per_file must be at least 1.")
    reader, total_pages = safe_load_pdf(input_pdf_path)
    selected_pages = parse_pages(pages_str, total_pages)
    if not selected_pages:
        raise ValueError("The selection contains no valid document pages.")
    base_name = os.path.splitext(os.path.basename(input_pdf_path))[0]
    suffix = make_windows_safe_suffix(selected_pages) if pages_str else ""
    chunk_ranges = build_chunk_ranges(selected_pages, pages_per_file)
    progress_data = build_progress_data(
        input_pdf_path,
        f"{base_name}{suffix}.md",
        total_pages,
        selected_pages,
    )
    progress_data["is_page_selection"] = bool(pages_str)
    progress_data["is_split"] = len(chunk_ranges) > 1 or progress_data["is_page_selection"]
    return reader, selected_pages, chunk_ranges, progress_data


def split_pdf(
    input_pdf_path: str,
    output_dir: str,
    pages_str: str = None,
    pages_per_file: int = 20,
) -> None:
    """
    Split selected pages into chunk PDFs and initialize progress.json.
    """
    reader, selected_pages, chunk_ranges, progress_data = prepare_split_state(
        input_pdf_path, pages_str, pages_per_file
    )
    prepare_output_dir(output_dir)
    print(f"Total PDF pages: {progress_data['total_pages']}")
    if pages_str:
        print(f"Selected pages count: {len(selected_pages)} " f"(pages: {pages_str})")

    initialize_chunks_state(reader, progress_data, chunk_ranges)

    progress_path = os.path.join(output_dir, "progress.json")
    write_json_atomic(progress_path, progress_data)

    write_chunk_pdfs_from_state(reader, output_dir, progress_data)

    print(f"\nWorkflow Initialized: {progress_path} created.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split PDF and init workflow.")
    parser.add_argument("input_pdf", help="Path to the source PDF file.")
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Specific pages/ranges to process, e.g., '5-15', '5,8,10-12'.",
    )
    parser.add_argument(
        "--pages_per_file", type=int, default=20, help="Pages per chunk."
    )
    parser.add_argument(
        "--info_only",
        action="store_true",
        help="Only show total pages / selected pages info.",
    )
    args = parser.parse_args()

    try:
        if args.info_only:
            total = get_pdf_info(args.input_pdf)
            if args.pages:
                selected = parse_pages(args.pages, total)
                print(f"Total pages: {total}")
                print(f"Selected pages: {len(selected)}")
            else:
                print(f"Total pages: {total}")
        else:
            split_pdf(args.input_pdf, "output_parts", args.pages, args.pages_per_file)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
