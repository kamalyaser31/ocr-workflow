import json
import os
import re
import sys
import argparse
import io
from pathlib import Path

# Ensure local scripts directory is in sys.path for importing _shared
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import write_json_atomic, write_text_atomic  # noqa: E402

PAGE_MARKER_PATTERN = re.compile(r"^--- Page ([0-9]+) ---\r?$", re.MULTILINE)


def _check_page_markers(output_text: str, start_page: int, end_page: int):
    """Check marker position, count, and sequence without I/O."""
    expected_pages = end_page - start_page + 1
    normalized_text = output_text.removeprefix("\ufeff")
    first_line = normalized_text.splitlines()[0] if normalized_text else ""
    markers = PAGE_MARKER_PATTERN.findall(normalized_text)
    expected_numbers = list(range(start_page, end_page + 1))
    actual_numbers = [int(marker) for marker in markers]
    expected_first_line = f"--- Page {start_page} ---"
    is_valid = (
        first_line == expected_first_line
        and len(markers) == expected_pages
        and actual_numbers == expected_numbers
    )
    return (
        is_valid,
        len(markers),
        expected_pages,
        actual_numbers,
        expected_numbers,
    )


def find_progress_chunk(progress_data: dict, part_num: int) -> dict:
    """Return the tracked chunk or fail rather than update unrelated state."""
    for chunk in progress_data["chunks"]:
        if chunk["part"] == part_num:
            return chunk
    raise ValueError(f"Part {part_num} is missing from progress.json.")


def save_failed_validation(
    progress_path, progress_data, part_info, marker_check
) -> None:
    """Persist a failed marker check and report its evidence."""
    _, found_count, expected_count, found_numbers, expected_numbers = marker_check
    find_progress_chunk(progress_data, part_info["part"])["status"] = "failed"
    write_json_atomic(progress_path, progress_data)
    print(
        f"Chunk {part_info['part']} failed validation. Markers found: "
        f"{found_count} / Expected: {expected_count}"
    )
    print(f"Numbers found: {found_numbers} / Expected: {expected_numbers}")


def save_validated_chunk(output_text, part_info, progress_path, raw_file_path) -> None:
    """Commit validated output and state before removing the raw file."""
    with open(progress_path, "r", encoding="utf-8") as file_handle:
        progress_data = json.load(file_handle)
    tracked_chunk = find_progress_chunk(progress_data, part_info["part"])
    output_filename = f"part_{part_info['part']}_output.md"
    output_path = os.path.join(os.path.dirname(progress_path), output_filename)
    write_text_atomic(output_path, output_text)
    tracked_chunk["status"] = "completed"
    tracked_chunk["output_file"] = output_filename
    write_json_atomic(progress_path, progress_data)
    if os.path.abspath(raw_file_path) != os.path.abspath(output_path):
        os.remove(raw_file_path)
        print(f"Raw temp file {raw_file_path} deleted.")
    print(f"Chunk {part_info['part']} saved as {output_filename}.")


def validate_chunk(output_text, part_info, progress_path, output_file_path):
    marker_check = _check_page_markers(
        output_text, part_info["start_page"], part_info["end_page"]
    )
    is_valid = marker_check[0]

    with open(progress_path, "r", encoding="utf-8") as file_handle:
        progress_data = json.load(file_handle)
    find_progress_chunk(progress_data, part_info["part"])

    if not is_valid:
        save_failed_validation(
            progress_path,
            progress_data,
            part_info,
            marker_check,
        )
        return False
    save_validated_chunk(output_text, part_info, progress_path, output_file_path)
    return True


def resolve_temp_path(part_num, output_dir):
    """Derive the temporary Markdown path for a part."""
    return os.path.join(output_dir, f"part_{part_num}_temp.md")


def validate_tracked_part(part_num, chunk_map, progress_path, output_dir):
    """Validate one requested part and return its outcome label."""
    part_info = chunk_map.get(part_num)
    if not part_info:
        print(
            f"[SKIP] Part {part_num} not found in progress.json",
            file=sys.stderr,
        )
        return "skipped"

    # استثناء الأجزاء المكتملة مسبقاً إذا كان ملفها الناتج موجوداً
    if part_info.get("status") == "completed" and part_info.get("output_file"):
        output_path = os.path.join(
            os.path.dirname(progress_path), part_info["output_file"]
        )
        if os.path.exists(output_path):
            print(
                f"[INFO] Part {part_num} already validated and saved as "
                f"{part_info['output_file']}"
            )
            return "passed"

    temp_path = resolve_temp_path(part_num, output_dir)
    if not os.path.exists(temp_path):
        print(
            f"[SKIP] Part {part_num}: temp file not found at {temp_path}",
            file=sys.stderr,
        )
        return "skipped"
    with open(temp_path, "r", encoding="utf-8") as file_handle:
        output_text = file_handle.read()
    if validate_chunk(output_text, part_info, progress_path, temp_path):
        return "passed"
    return "failed"


def run_validation(part_nums, progress_path, output_dir):
    """Validate parts and return passed, failed, and skipped counts."""
    if not os.path.exists(progress_path):
        raise FileNotFoundError(f"Progress file not found at {progress_path}")

    with open(progress_path, "r", encoding="utf-8") as f:
        progress_data = json.load(f)

    chunk_map = {chunk["part"]: chunk for chunk in progress_data["chunks"]}
    outcomes = [
        validate_tracked_part(part_num, chunk_map, progress_path, output_dir)
        for part_num in part_nums
    ]
    return (
        outcomes.count("passed"),
        outcomes.count("failed"),
        outcomes.count("skipped"),
    )


if __name__ == "__main__":
    # Force UTF-8 for stdout to prevent Windows encoding errors
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Validate chunk page markers against progress.json."
    )
    parser.add_argument(
        "parts",
        nargs="*",
        type=int,
        help="Part number(s) to validate (e.g. 1 3 5).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="validate_all",
        help="Validate all parts that have a temp file (part_N_temp.md).",
    )
    parser.add_argument(
        "--progress",
        default=os.path.join("output_parts", "progress.json"),
        help="Path to progress.json (default: output_parts/progress.json).",
    )
    parser.add_argument(
        "--output-dir",
        default="output_parts",
        help="Directory containing temp files (default: output_parts).",
    )

    args = parser.parse_args()

    # Resolve output directory dynamically relative to progress path if default
    progress_file = Path(args.progress).resolve()
    if args.output_dir == "output_parts":
        output_dir = str(progress_file.parent)
    else:
        output_dir = args.output_dir

    # Determine which parts to validate
    if args.validate_all:
        if not progress_file.exists():
            print(f"Error: Progress file not found at {progress_file}")
            sys.exit(1)
        with open(progress_file, "r", encoding="utf-8") as f:
            progress_data = json.load(f)
        part_nums = [chunk["part"] for chunk in progress_data["chunks"]]
        if not part_nums:
            print("Error: No chunks found in progress.json.", file=sys.stderr)
            sys.exit(1)
        print(f"Validating all tracked parts: {part_nums}")
    elif args.parts:
        part_nums = args.parts
    else:
        parser.print_help()
        sys.exit(1)

    try:
        passed, failed, skipped = run_validation(
            part_nums, str(progress_file), output_dir
        )
    except (FileNotFoundError, OSError, ValueError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*40}")
    print(
        f"Results: {passed} passed, {failed} failed, {skipped} skipped, "
        f"{passed + failed + skipped} total"
    )
    print(f"{'='*40}")

    all_requested_parts_passed = passed > 0 and failed == 0 and skipped == 0
    sys.exit(0 if all_requested_parts_passed else 1)
