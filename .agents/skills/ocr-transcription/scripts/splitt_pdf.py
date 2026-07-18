import os
import json
import argparse
import sys
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


def write_json_atomic(file_path: str, payload: dict) -> None:
    """Replace a JSON state file only after its new contents are durable."""
    temp_path = f"{file_path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, indent=4, ensure_ascii=False)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temp_path, file_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


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
                f"The PDF file '{input_pdf_path}' contains 0 pages or is "
                "corrupted."
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


def parse_page_range(page_range: str, total_pages: int) -> set:
    """Return the document intersection of one page range."""
    if page_range.count("-") != 1:
        print(f"Warning: Ignoring malformed range '{page_range}'.")
        return set()
    start_text, end_text = (side.strip() for side in page_range.split("-"))
    try:
        start_page = int(start_text) if start_text else 1
        end_page = int(end_text) if end_text else total_pages
    except ValueError:
        print(f"Warning: Ignoring nonnumeric range '{page_range}'.")
        return set()
    if start_page < 1 or end_page > total_pages:
        print(
            f"Warning: Range '{page_range}' exceeds 1 to {total_pages}; "
            "outside pages are ignored.",
            file=sys.stderr,
        )
    bounded_start = max(1, start_page)
    bounded_end = min(total_pages, end_page)
    if bounded_start <= bounded_end:
        return set(range(bounded_start, bounded_end + 1))
    print(f"Warning: Range '{page_range}' has no document pages.")
    return set()


def parse_page_number(page_text: str, total_pages: int) -> set:
    """Return one valid page number or an empty set."""
    try:
        page_number = int(page_text)
    except ValueError:
        print(f"Warning: Ignoring nonnumeric page token '{page_text}'.")
        return set()
    if 1 <= page_number <= total_pages:
        return {page_number}
    print(f"Warning: Page '{page_number}' is outside 1 to {total_pages}.")
    return set()


def parse_pages(pages_str: str, total_pages: int) -> list:
    """Parse a page selection into sorted unique 1-indexed pages."""
    if not pages_str:
        return list(range(1, total_pages + 1))
    selected_pages = set()
    for page_token in pages_str.split(","):
        page_token = page_token.strip()
        if not page_token:
            continue
        if "-" in page_token:
            selected_pages.update(parse_page_range(page_token, total_pages))
        else:
            selected_pages.update(parse_page_number(page_token, total_pages))
    return sorted(selected_pages)


def group_contiguous(pages: list) -> list:
    """
    Groups sorted unique page numbers into (start, end) tuples.
    For example: [5, 8, 10, 11, 12] -> [(5, 5), (8, 8), (10, 12)]
    """
    if not pages:
        return []

    ranges = []
    start = pages[0]
    prev = pages[0]

    for page in pages[1:]:
        if page == prev + 1:
            prev = page
        else:
            ranges.append((start, prev))
            start = page
            prev = page

    ranges.append((start, prev))
    return ranges


def make_windows_safe_suffix(selected_pages: list) -> str:
    """Build a canonical suffix from validated page numbers."""
    if not selected_pages:
        return ""
    range_labels = []
    for start_page, end_page in group_contiguous(selected_pages):
        if start_page == end_page:
            range_labels.append(str(start_page))
        else:
            range_labels.append(f"{start_page}-{end_page}")
    return f"_p{'_'.join(range_labels)}"


def build_chunk_ranges(selected_pages: list, pages_per_file: int) -> list:
    """Split contiguous selections into bounded chunk ranges."""
    chunk_ranges = []
    for range_start, range_end in group_contiguous(selected_pages):
        chunk_start = range_start
        while chunk_start <= range_end:
            chunk_end = min(chunk_start + pages_per_file - 1, range_end)
            chunk_ranges.append((chunk_start, chunk_end))
            chunk_start = chunk_end + 1
    return chunk_ranges


def new_chunk(part, filename, start_page, end_page) -> dict:
    """Create one pending progress record."""
    return {
        "part": part,
        "filename": filename,
        "start_page": start_page,
        "end_page": end_page,
        "status": "pending",
        "output_file": "",
    }


def write_chunk_pdf(reader, page_range, output_path: str) -> None:
    """Write one selected PDF range."""
    start_page, end_page = page_range
    writer = PdfWriter()
    for page_index in range(start_page - 1, end_page):
        writer.add_page(reader.pages[page_index])
    with open(output_path, "wb") as output_file:
        writer.write(output_file)


def _write_chunk_pdfs(reader, chunk_ranges, base_name, output_dir) -> list:
    """Write chunk PDFs and return their progress records."""
    chunk_records = []
    for part_number, page_range in enumerate(chunk_ranges, start=1):
        start_page, end_page = page_range
        filename = (
            f"{base_name}_part_{part_number}_p{start_page}-{end_page}.pdf"
        )
        write_chunk_pdf(reader, page_range, os.path.join(output_dir, filename))
        chunk_records.append(
            new_chunk(part_number, filename, start_page, end_page)
        )
        print(f"Created: {filename} (Pages: {start_page} to {end_page})")
    return chunk_records


def build_progress_data(
    input_pdf_path, final_filename, total_pages, selected_pages
) -> dict:
    """Create workflow state before chunk records are appended."""
    return {
        "source_file": input_pdf_path,
        "final_filename": final_filename,
        "total_pages": total_pages,
        "total_selected_pages": len(selected_pages),
        "is_page_selection": False,
        "is_split": False,
        "chunks": [],
    }


def initialize_chunks(reader, output_dir, chunk_ranges, progress_data) -> None:
    """Track the original PDF or create selected chunk PDFs."""
    input_pdf_path = progress_data["source_file"]
    if len(chunk_ranges) == 1 and not progress_data["is_page_selection"]:
        progress_data["chunks"].append(
            new_chunk(
                1, os.path.basename(input_pdf_path), 1, len(reader.pages)
            )
        )
        print("Single file workflow initialized (no splitting).")
        return
    base_name = os.path.splitext(os.path.basename(input_pdf_path))[0]
    print(f"Number of output chunk files: {len(chunk_ranges)}")
    progress_data["chunks"].extend(
        _write_chunk_pdfs(reader, chunk_ranges, base_name, output_dir)
    )


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
    progress_data["is_split"] = len(chunk_ranges) > 1
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
        print(
            f"Selected pages count: {len(selected_pages)} "
            f"(pages: {pages_str})"
        )
    initialize_chunks(reader, output_dir, chunk_ranges, progress_data)

    progress_path = os.path.join(output_dir, "progress.json")
    write_json_atomic(progress_path, progress_data)

    print(f"\nWorkflow Initialized: {progress_path} created.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Split PDF and init workflow."
    )
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
            split_pdf(
                args.input_pdf, "output_parts", args.pages, args.pages_per_file
            )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
