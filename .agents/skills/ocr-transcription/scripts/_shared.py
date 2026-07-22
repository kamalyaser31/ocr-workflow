"""Shared utilities and helpers for OCR transcription scripts."""

import json
import os
import sys
from pathlib import Path


def write_json_atomic(path: Path | str, payload: dict) -> None:
    """Persist state only after the complete JSON document is written."""
    target_path = Path(path)
    temporary_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
    try:
        # Ensure parent directory exists
        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, ensure_ascii=False, indent=4)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, target_path)
    finally:
        try:
            if temporary_path.exists():
                temporary_path.unlink()
        except OSError:
            pass


def write_text_atomic(path: Path | str, content: str) -> None:
    """Replace a text file only after its complete contents are durable."""
    target_path = Path(path)
    temporary_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
    try:
        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("w", encoding="utf-8") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, target_path)
    finally:
        try:
            if temporary_path.exists():
                temporary_path.unlink()
        except OSError:
            pass


def parse_page_range(page_range: str, total_pages: int) -> set[int]:
    """Return the document intersection of one page range."""
    if page_range.count("-") != 1:
        print(f"Warning: Ignoring malformed range '{page_range}'.", file=sys.stderr)
        return set()
    start_text, end_text = (side.strip() for side in page_range.split("-"))
    try:
        start_page = int(start_text) if start_text else 1
        end_page = int(end_text) if end_text else total_pages
    except ValueError:
        print(f"Warning: Ignoring nonnumeric range '{page_range}'.", file=sys.stderr)
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
    print(f"Warning: Range '{page_range}' has no document pages.", file=sys.stderr)
    return set()


def parse_page_number(page_text: str, total_pages: int) -> set[int]:
    """Return one valid page number or an empty set."""
    try:
        page_number = int(page_text)
    except ValueError:
        print(
            f"Warning: Ignoring nonnumeric page token '{page_text}'.",
            file=sys.stderr,
        )
        return set()
    if 1 <= page_number <= total_pages:
        return {page_number}
    print(
        f"Warning: Page '{page_number}' is outside 1 to {total_pages}.",
        file=sys.stderr,
    )
    return set()


def parse_pages(pages_str: str | None, total_pages: int) -> list[int]:
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

