import os
import json
import argparse
import sys
from pypdf import PdfReader, PdfWriter

def safe_load_pdf(input_pdf_path: str):
    """Safely loads a PDF file, checking for corruption, encryption, and empty files."""
    if not os.path.exists(input_pdf_path):
        print(f"Error: PDF file not found at '{input_pdf_path}'")
        sys.exit(1)
    try:
        reader = PdfReader(input_pdf_path)
        if reader.is_encrypted:
            print(f"Error: The PDF file '{input_pdf_path}' is encrypted or password-protected.")
            sys.exit(1)
        total_pages = len(reader.pages)
        if total_pages == 0:
            print(f"Error: The PDF file '{input_pdf_path}' contains 0 pages or is corrupted.")
            sys.exit(1)
        return reader, total_pages
    except Exception as e:
        print(f"Error: Failed to read PDF file '{input_pdf_path}'. It may be corrupted or invalid. Detail: {e}")
        sys.exit(1)

def get_pdf_info(input_pdf_path: str):
    """Returns the total number of pages in the PDF."""
    _, total_pages = safe_load_pdf(input_pdf_path)
    return total_pages

def parse_pages(pages_str: str, total_pages: int) -> list:
    """
    Parses a page selection string like "5-15", "5,8,10-12", "10-" or "-20"
    and returns a sorted list of unique 1-indexed page numbers.
    Prints warnings for malformed inputs.
    """
    if not pages_str:
        return list(range(1, total_pages + 1))
        
    selected_pages = set()
    parts = pages_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        if "-" in part:
            if part.count("-") > 1:
                print(f"Warning: Ignoring malformed page range '{part}' (multiple dashes detected).")
                continue
                
            split_parts = part.split("-")
            start_str, end_str = split_parts
            start_str = start_str.strip()
            end_str = end_str.strip()
            
            try:
                start = int(start_str) if start_str else 1
                end = int(end_str) if end_str else total_pages
                
                # Check bounds and warn if necessary
                if start < 1 or start > total_pages or end < 1 or end > total_pages:
                    print(f"Warning: Page range '{part}' exceeds document bounds (1 to {total_pages}). Out-of-bounds parts will be ignored.")
                
                start_clamped = max(1, min(start, total_pages))
                end_clamped = max(1, min(end, total_pages))
                
                if start_clamped <= end_clamped:
                    selected_pages.update(range(start_clamped, end_clamped + 1))
                else:
                    print(f"Warning: Ignoring invalid range '{part}' (start page is greater than end page).")
            except ValueError:
                print(f"Warning: Ignoring malformed page range '{part}' (non-numeric values found).")
        else:
            try:
                page = int(part)
                if 1 <= page <= total_pages:
                    selected_pages.add(page)
                else:
                    print(f"Warning: Ignoring page number '{page}' (out of document bounds 1 to {total_pages}).")
            except ValueError:
                print(f"Warning: Ignoring invalid page token '{part}' (not a number).")
                
    return sorted(list(selected_pages))

def group_contiguous(pages: list) -> list:
    """
    Groups a sorted list of unique page numbers into a list of tuples (start, end).
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

def make_windows_safe_suffix(pages_str: str) -> str:
    """Generates a safe filename suffix for page range selection."""
    if not pages_str:
        return ""
    safe_str = pages_str.replace(",", "_").replace(" ", "")
    return f"_p{safe_str}"

def split_pdf(input_pdf_path: str, output_dir: str, pages_str: str = None, pages_per_file: int = 20) -> None:
    """
    Split selected pages of a PDF into contiguous parts, saving them as chunk PDFs
    and initializing the progress.json tracker.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    reader, total_pages = safe_load_pdf(input_pdf_path)
    base_name = os.path.splitext(os.path.basename(input_pdf_path))[0]
    
    # Parse selected pages
    selected_pages = parse_pages(pages_str, total_pages)
    total_selected = len(selected_pages)
    
    suffix = make_windows_safe_suffix(pages_str)
    final_filename = f"{base_name}{suffix}.md"
    
    # Group selected pages into contiguous ranges
    contiguous_ranges = group_contiguous(selected_pages)
    
    # Split contiguous ranges into chunks of max size pages_per_file
    chunks_pages = []
    for start, end in contiguous_ranges:
        length = end - start + 1
        num_sub_chunks = (length + pages_per_file - 1) // pages_per_file
        for i in range(num_sub_chunks):
            sub_start = start + i * pages_per_file
            sub_end = min(sub_start + pages_per_file - 1, end)
            chunks_pages.append((sub_start, sub_end))
            
    num_files = len(chunks_pages)
    # is_split is True if we have multiple chunks OR if we are processing a subset of the full PDF
    is_split = (num_files > 1)
    
    progress_data = {
        "source_file": input_pdf_path,
        "final_filename": final_filename,
        "total_pages": total_pages,
        "total_selected_pages": total_selected,
        "is_page_selection": bool(pages_str),
        "is_split": is_split,
        "chunks": []
    }

    print(f"Total PDF pages: {total_pages}")
    if pages_str:
        print(f"Selected pages count: {total_selected} (pages: {pages_str})")
    
    # If single chunk and full file, we don't necessarily have to write a new PDF,
    # but if it's a page selection, we must write a chunk PDF containing only those pages.
    if num_files == 1 and not pages_str:
        progress_data["chunks"].append({
            "part": 1,
            "filename": os.path.basename(input_pdf_path),
            "start_page": 1,
            "end_page": total_pages,
            "status": "pending",
            "output_file": ""
        })
        print("Single file workflow initialized (no splitting).")
    else:
        print(f"Number of output chunk files: {num_files}")
        for idx, (start_page, end_page) in enumerate(chunks_pages):
            writer = PdfWriter()
            # pypdf pages are 0-indexed, start_page and end_page are 1-indexed
            for page_num in range(start_page - 1, end_page):
                writer.add_page(reader.pages[page_num])

            output_filename = f"{base_name}_part_{idx + 1}_p{start_page}-{end_page}.pdf"
            output_path = os.path.join(output_dir, output_filename)
            
            with open(output_path, "wb") as f_out:
                writer.write(f_out)

            progress_data["chunks"].append({
                "part": idx + 1,
                "filename": output_filename,
                "start_page": start_page,
                "end_page": end_page,
                "status": "pending",
                "output_file": ""
            })
            print(f"Created: {output_filename} (Original Pages: {start_page} to {end_page})")

    # Save the progress tracker
    progress_path = os.path.join(output_dir, "progress.json")
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=4, ensure_ascii=False)
    
    print(f"\nWorkflow Initialized: {progress_path} created.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split PDF and init workflow.")
    parser.add_argument("input_pdf", help="Path to the source PDF file.")
    parser.add_argument("--pages", type=str, default=None, help="Specific pages/ranges to process, e.g., '5-15', '5,8,10-12'.")
    parser.add_argument("--pages_per_file", type=int, default=20, help="Pages per chunk.")
    parser.add_argument("--info_only", action="store_true", help="Only show total pages / selected pages info.")
    args = parser.parse_args()

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

