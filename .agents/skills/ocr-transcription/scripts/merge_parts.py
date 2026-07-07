import json
import os
import shutil
import re

def extract_page_samples(file_path, lines_count=8, interval=20):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return None

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split content by page markers
    pages = re.split(r'(--- Page \d+ ---)', content)
    
    # pages[0] is everything before the first marker (usually empty)
    # pages[1] is the first marker, pages[2] is the first page content, and so on.
    
    samples_data = []
    
    for i in range(1, len(pages), 2):
        marker = pages[i]
        page_content = pages[i+1]
        
        # Extract page number
        match = re.search(r'--- Page (\d+) ---', marker)
        if match:
            page_num = int(match.group(1))
            
            # Check if page number is in the sequence (1, 21, 41, ...)
            if (page_num - 1) % interval == 0:
                # Get first N non-empty lines
                lines = [line.strip() for line in page_content.strip().split('\n') if line.strip()]
                sample_lines = lines[:lines_count]
                content_text = "\n".join(sample_lines)
                
                samples_data.append({
                    "page": page_num,
                    "marker": marker,
                    "content": content_text
                })

    if not samples_data:
        return None

    # Check for duplicates
    seen_contents = {}
    duplicates = []
    
    for sample in samples_data:
        if sample["content"] in seen_contents:
            duplicates.append((seen_contents[sample["content"]], sample["page"]))
        else:
            seen_contents[sample["content"]] = sample["page"]

    report = "\n" + "="*50 + "\n"
    report += "DUPLICATION CHECK REPORT:\n"
    if duplicates:
        report += "⚠️ WARNING: Duplicates found in the following pages:\n"
        for original, dup in duplicates:
            report += f"  - Page {dup} matches content of Page {original}\n"
    else:
        report += "✅ SUCCESS: No duplicates found in the samples.\n"
    report += "="*50 + "\n\n"

    samples_text = "\n\n".join([f"{s['marker']}\n{s['content']}" for s in samples_data])
    return report + samples_text + "\n" + "="*50


def merge_parts(progress_path, final_output_name_override=None):
    """
    Reads progress.json, merges completed part files into a single final file,
    and then deletes the individual verified part files.
    """
    if not os.path.exists(progress_path):
        print(f"Error: {progress_path} not found.")
        return

    with open(progress_path, "r", encoding="utf-8") as f:
        progress_data = json.load(f)

    is_split = progress_data.get("is_split", True)
    chunks = progress_data.get("chunks", [])
    
    # Use final_filename from progress.json if available, or fallback to override or default
    final_output_name = final_output_name_override or progress_data.get("final_filename", "final_full_ocr_output.md")

    if not chunks:
        print("No chunks found in progress.json.")
        return

    # Check if all chunks are completed
    pending = [c["part"] for c in chunks if c["status"] != "completed"]
    if pending:
        print(f"Error: The following parts are not yet completed: {pending}")
        print("Merge aborted. All parts must be 'completed' before merging.")
        return

    output_parts_dir = os.path.dirname(progress_path)
    final_output_path = os.path.join("md", final_output_name) # Save in md/ directory

    # Ensure destination directory exists
    dest_dir = os.path.dirname(final_output_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    if not is_split:
        # For single file workflow, just copy the validated part to the final location
        chunk = chunks[0]
        part_file_path = os.path.join(output_parts_dir, chunk["output_file"])
        if os.path.exists(part_file_path):
            shutil.copy2(part_file_path, final_output_path)
            print(f"File moved as: {final_output_path}")
        else:
            print(f"Error: Validated part file {part_file_path} not found.")
            return
    else:
        # Merge logic for split files
        with open(final_output_path, "w", encoding="utf-8") as f_final:
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

    print(f"\nSuccess: Process complete. Final file: '{final_output_path}'")

    # Run Duplication Check before cleanup
    print("\nRunning duplication check on the final merged file...")
    try:
        result = extract_page_samples(final_output_path)
        if result:
            # Write preview file
            sampling_file = "sampling_preview.txt"
            with open(sampling_file, "w", encoding='utf-8') as f:
                f.write(result)
            
            if "WARNING:" in result:
                print("\n" + "="*50)
                print("WARNING: Duplication detected in the merged file!")
                # Extract and print only the report part
                report_part = result.split("==================================================")[1].strip()
                print(report_part)
                print("="*50 + "\n")
                print("Cleanup aborted. Intermediate files have been preserved for debugging.")
                return False
            else:
                print("Duplication check passed. No duplicates found.")
    except Exception as e:
        print(f"Warning: Could not run duplication check due to error: {e}")

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
            # Only delete if it's a split part (don't delete original source PDF)
            if is_split and os.path.exists(part_pdf_path):
                os.remove(part_pdf_path)
                print(f"Deleted PDF chunk: {chunk['filename']}")

    print("Cleanup complete.")

    # Final Stage: Delete progress.json and sampling_preview.txt
    if os.path.exists(progress_path):
        os.remove(progress_path)
        print(f"Deleted progress record: {progress_path}")

    sampling_file = "sampling_preview.txt"
    if os.path.exists(sampling_file):
        os.remove(sampling_file)
        print(f"Deleted sampling preview: {sampling_file}")
    
    return True

if __name__ == "__main__":
    import sys
    # Force UTF-8 for stdout to prevent Windows encoding errors
    if sys.stdout.encoding != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    progress_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join("output_parts", "progress.json")
    final_name = sys.argv[2] if len(sys.argv) > 2 else None
    
    success = merge_parts(progress_file, final_name)
    sys.exit(0 if success is not False else 1)
