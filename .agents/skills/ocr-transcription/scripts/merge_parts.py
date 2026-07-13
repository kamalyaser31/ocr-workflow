import json
import os
import shutil
import re
import argparse
import sys

def extract_page_samples(file_path, lines_count=8, interval=1):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return None, False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split content by page markers
    pages = re.split(r'(--- Page \d+ ---)', content)
    
    samples_data = []
    
    for i in range(1, len(pages), 2):
        marker = pages[i]
        page_content = pages[i+1]
        
        # Extract page number
        match = re.search(r'--- Page (\d+) ---', marker)
        if match:
            page_num = int(match.group(1))
            
            # Check if page number is in the sequence (check all pages by default)
            if (page_num - 1) % interval == 0:
                # Clean page content to check length
                cleaned_content = page_content.strip()
                if len(cleaned_content) > 120:
                    # Get first N non-empty lines
                    lines = [line.strip() for line in cleaned_content.split('\n') if line.strip()]
                    sample_lines = lines[:lines_count]
                    content_text = "\n".join(sample_lines)
                    
                    samples_data.append({
                        "page": page_num,
                        "marker": marker,
                        "content": content_text
                    })

    if not samples_data:
        return "No pages with sufficient text (>120 chars) to run duplication check.\n", False

    # Check for duplicates
    seen_contents = {}
    duplicates = []
    
    for sample in samples_data:
        if sample["content"] in seen_contents:
            duplicates.append((seen_contents[sample["content"]], sample["page"]))
        else:
            seen_contents[sample["content"]] = sample["page"]

    has_duplicates = len(duplicates) > 0

    report = "\n" + "="*50 + "\n"
    report += "DUPLICATION CHECK REPORT:\n"
    if has_duplicates:
        report += "⚠️ WARNING: Duplicates found in the following pages:\n"
        for original, dup in duplicates:
            report += f"  - Page {dup} matches content of Page {original}\n"
    else:
        report += "✅ SUCCESS: No duplicates found in the samples.\n"
    report += "="*50 + "\n\n"

    samples_text = "\n\n".join([f"{s['marker']}\n{s['content']}" for s in samples_data])
    return report + samples_text + "\n" + "="*50, has_duplicates


def merge_parts(progress_path, final_output_name_override=None):
    """
    Reads progress.json, merges completed part files into a single temporary final file,
    runs duplication check on the temp merged file, and on success moves it to the final
    destination and cleans up intermediate files. On failure, preserves intermediate files
    and renames the temp merged file to unverified_merged.md.
    """
    if not os.path.exists(progress_path):
        print(f"Error: {progress_path} not found.")
        return False

    with open(progress_path, "r", encoding="utf-8") as f:
        progress_data = json.load(f)

    is_split = progress_data.get("is_split", True)
    is_page_selection = progress_data.get("is_page_selection", False)
    chunks = progress_data.get("chunks", [])
    
    # Use final_filename from progress.json if available, or fallback to override or default
    final_output_name = final_output_name_override or progress_data.get("final_filename", "final_full_ocr_output.md")

    if not chunks:
        print("No chunks found in progress.json.")
        return False

    # Check if all chunks are completed
    pending = [c["part"] for c in chunks if c["status"] != "completed"]
    if pending:
        print(f"Error: The following parts are not yet completed: {pending}")
        print("Merge aborted. All parts must be 'completed' before merging.")
        return False

    output_parts_dir = os.path.dirname(progress_path)
    
    # Calculate Project Root and final paths absolute to it
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(progress_path)))
    final_output_path = os.path.join(project_root, "md", final_output_name)
    
    # Temp merged file in output_parts directory
    temp_merged_path = os.path.join(output_parts_dir, "temp_merged.md")

    # Ensure final destination directory exists
    dest_dir = os.path.dirname(final_output_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    if not is_split:
        # For single file workflow, copy the validated part to the temporary location
        chunk = chunks[0]
        part_file_path = os.path.join(output_parts_dir, chunk["output_file"])
        if os.path.exists(part_file_path):
            shutil.copy2(part_file_path, temp_merged_path)
        else:
            print(f"Error: Validated part file {part_file_path} not found.")
            return False
    else:
        # Merge logic into temp_merged_path
        with open(temp_merged_path, "w", encoding="utf-8") as f_final:
            for chunk in chunks:
                if chunk["status"] == "completed" and chunk["output_file"]:
                    part_file_path = os.path.join(output_parts_dir, chunk["output_file"])
                    if os.path.exists(part_file_path):
                        with open(part_file_path, "r", encoding="utf-8") as f_part:
                            f_final.write(f_part.read())
                            f_final.write("\n\n") # Spacing between chunks
                        print(f"Merged: {chunk['output_file']}")
                    else:
                        print(f"Warning: File {part_file_path} missing for chunk {chunk['part']}")

    print(f"\nSuccess: Chunks merged into temporary file.")

    # Run Duplication Check
    duplication_failed = False
    if not is_page_selection:
        print("\nRunning duplication check on the merged content...")
        try:
            report_text, has_duplicates = extract_page_samples(temp_merged_path)
            if report_text:
                # Write preview file in output_parts
                sampling_file = os.path.join(output_parts_dir, "sampling_preview.txt")
                with open(sampling_file, "w", encoding='utf-8') as f:
                    f.write(report_text)
                
                if has_duplicates:
                    print("\n" + "="*50)
                    print("WARNING: Duplication detected in the merged file!")
                    # Print only the warning lines
                    for line in report_text.split("\n"):
                        if "WARNING" in line or "matches content" in line:
                            print(line)
                    print("="*50 + "\n")
                    duplication_failed = True
                else:
                    print("Duplication check passed. No duplicates found.")
        except (OSError, UnicodeDecodeError) as e:
            print(f"Error: Duplication check failed unexpectedly: {e}")
            duplication_failed = True
    else:
        print("\nSkipping duplication check (page selection workflow).")

    if duplication_failed:
        # Rename temp merged file to mark it as unverified draft in output_parts
        unverified_path = os.path.join(output_parts_dir, "unverified_merged.md")
        if os.path.exists(temp_merged_path):
            if os.path.exists(unverified_path):
                os.remove(unverified_path)
            os.rename(temp_merged_path, unverified_path)
            print(f"Unverified merged file saved as: '{unverified_path}'")
        print("Cleanup aborted. Intermediate files have been preserved for debugging.")
        return False

    # If duplication check passed (or skipped), move temp merged file to final destination
    if os.path.exists(temp_merged_path):
        if os.path.exists(final_output_path):
            os.remove(final_output_path)
        shutil.move(temp_merged_path, final_output_path)
        print(f"Final verified file moved to: '{final_output_path}'")

    # Final Cleanup: Delete verified part files (Markdown) AND source chunk files (PDF)
    print("\nStarting final cleanup of verified part files and PDF chunks...")
    for chunk in chunks:
        if chunk["status"] == "completed":
            # 1. Delete Markdown output part
            if chunk["output_file"]:
                part_md_path = os.path.join(output_parts_dir, chunk["output_file"])
                if os.path.exists(part_md_path):
                    os.remove(part_md_path)
                    print(f"Deleted Markdown part: {chunk['output_file']}")
            
            # 2. Delete PDF chunk file
            part_pdf_path = os.path.join(output_parts_dir, chunk["filename"])
            if is_split and os.path.exists(part_pdf_path):
                os.remove(part_pdf_path)
                print(f"Deleted PDF chunk: {chunk['filename']}")

    print("Cleanup complete.")

    # Final Stage: Delete progress.json and sampling_preview.txt
    if os.path.exists(progress_path):
        os.remove(progress_path)
        print(f"Deleted progress record: {progress_path}")

    sampling_file = os.path.join(output_parts_dir, "sampling_preview.txt")
    if os.path.exists(sampling_file):
        os.remove(sampling_file)
        print(f"Deleted sampling preview: {sampling_file}")
    
    return True

if __name__ == "__main__":
    # Force UTF-8 for stdout to prevent Windows encoding errors
    if sys.stdout.encoding != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description="Merge completed OCR parts and run duplication checks.")
    parser.add_argument(
        "--progress", default=os.path.join("output_parts", "progress.json"),
        help="Path to progress.json tracker file (default: output_parts/progress.json)."
    )
    parser.add_argument(
        "--output-name", default=None,
        help="Optional override for the final merged Markdown output file name."
    )
    args = parser.parse_args()
    
    success = merge_parts(args.progress, args.output_name)
    sys.exit(0 if success is not False else 1)
