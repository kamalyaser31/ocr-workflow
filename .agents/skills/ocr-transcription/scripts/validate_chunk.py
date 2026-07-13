import json
import os
import re
import sys
import argparse
import io

def _check_page_markers(output_text: str, start_page: int, end_page: int):
    """Pure validation logic: checks page markers count and sequence. No I/O."""
    expected_pages = end_page - start_page + 1
    markers = re.findall(r"--- Page (\d+) ---", output_text)
    expected_numbers = list(range(start_page, end_page + 1))
    actual_numbers = [int(m) for m in markers]
    is_valid = (len(markers) == expected_pages) and (actual_numbers == expected_numbers)
    return is_valid, len(markers), expected_pages, actual_numbers, expected_numbers

def validate_chunk(output_text, part_info, progress_path, output_file_path):
    is_valid, markers_count, expected_pages, actual_numbers, expected_numbers = _check_page_markers(
        output_text, part_info["start_page"], part_info["end_page"]
    )
    
    # Update progress
    with open(progress_path, "r", encoding="utf-8") as f:
        progress_data = json.load(f)
        
    for chunk in progress_data["chunks"]:
        if chunk["part"] == part_info["part"]:
            if is_valid:
                chunk["status"] = "completed"
                output_filename = f"part_{part_info['part']}_output.md"
                output_path = os.path.join(os.path.dirname(progress_path), output_filename)
                with open(output_path, "w", encoding="utf-8") as f_out:
                    f_out.write(output_text)
                chunk["output_file"] = output_filename
                
                # Cleanup: Delete the raw temp file only if it is different from output_path
                if os.path.exists(output_file_path) and os.path.abspath(output_file_path) != os.path.abspath(output_path):
                    os.remove(output_file_path)
                    print(f"Raw temp file {output_file_path} deleted.")
                
                print(f"Chunk {part_info['part']} validated and saved as {output_filename}.")
            else:
                chunk["status"] = "failed"
                print(f"Chunk {part_info['part']} failed validation. Markers found: {markers_count} / Expected: {expected_pages}")
                print(f"Numbers found: {actual_numbers} / Expected: {expected_numbers}")
            break
            
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=4, ensure_ascii=False)
        
    return is_valid

def resolve_temp_path(part_num, output_dir):
    """Derives the temp file path from part number: output_parts/part_N_temp.md"""
    return os.path.join(output_dir, f"part_{part_num}_temp.md")


def run_validation(part_nums, progress_path, output_dir):
    """Validates a list of parts and returns (passed, failed, skipped) counts."""
    if not os.path.exists(progress_path):
        print(f"Error: Progress file not found at {progress_path}")
        return 0, 0, 0

    with open(progress_path, "r", encoding="utf-8") as f:
        progress_data = json.load(f)

    # Build a lookup for quick access
    chunk_map = {chunk["part"]: chunk for chunk in progress_data["chunks"]}

    passed = 0
    failed = 0
    skipped = 0

    for part_num in part_nums:
        part_info = chunk_map.get(part_num)
        if not part_info:
            print(f"[SKIP] Part {part_num} not found in progress.json")
            skipped += 1
            continue

        temp_path = resolve_temp_path(part_num, output_dir)
        if not os.path.exists(temp_path):
            print(f"[SKIP] Part {part_num}: temp file not found at {temp_path}")
            skipped += 1
            continue

        with open(temp_path, "r", encoding="utf-8") as f:
            output_text = f.read()

        success = validate_chunk(output_text, part_info, progress_path, temp_path)
        if success:
            passed += 1
        else:
            failed += 1

    return passed, failed, skipped


if __name__ == "__main__":
    # Force UTF-8 for stdout to prevent Windows encoding errors
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(
        description="Validate transcribed chunks against progress.json page markers."
    )
    parser.add_argument(
        "parts", nargs="*", type=int,
        help="Part number(s) to validate (e.g. 1 3 5)."
    )
    parser.add_argument(
        "--all", action="store_true", dest="validate_all",
        help="Validate all parts that have a temp file (part_N_temp.md)."
    )
    parser.add_argument(
        "--progress", default=os.path.join("output_parts", "progress.json"),
        help="Path to progress.json (default: output_parts/progress.json)."
    )
    parser.add_argument(
        "--output-dir", default="output_parts",
        help="Directory containing temp files (default: output_parts)."
    )

    args = parser.parse_args()

    # Determine which parts to validate
    if args.validate_all:
        if not os.path.exists(args.progress):
            print(f"Error: Progress file not found at {args.progress}")
            sys.exit(1)
        with open(args.progress, "r", encoding="utf-8") as f:
            progress_data = json.load(f)
        # Collect all parts that have a temp file on disk
        part_nums = []
        for chunk in progress_data["chunks"]:
            temp_path = resolve_temp_path(chunk["part"], args.output_dir)
            if os.path.exists(temp_path):
                part_nums.append(chunk["part"])
        if not part_nums:
            print("No temp files found to validate.")
            sys.exit(0)
        print(f"Validating all parts with temp files: {part_nums}")
    elif args.parts:
        part_nums = args.parts
    else:
        parser.print_help()
        sys.exit(1)

    passed, failed, skipped = run_validation(part_nums, args.progress, args.output_dir)

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped, {passed + failed + skipped} total")
    print(f"{'='*40}")

    sys.exit(0 if failed == 0 else 1)
