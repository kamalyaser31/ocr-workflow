import os
import json
import argparse
from pypdf import PdfReader, PdfWriter

def get_pdf_info(input_pdf_path: str):
    """Returns the total number of pages in the PDF."""
    reader = PdfReader(input_pdf_path)
    return len(reader.pages)

def split_pdf(input_pdf_path: str, output_dir: str, pages_per_file: int = 20) -> None:
    """
    Split a PDF into multiple files with a fixed number of pages each
    and initialize a progress.json tracker.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    reader = PdfReader(input_pdf_path)
    total_pages = len(reader.pages)
    base_name = os.path.splitext(os.path.basename(input_pdf_path))[0]
    final_filename = f"{base_name}.md"

    # Rule: If PDF <= 20 pages, do not split.
    if total_pages <= 20:
        print(f"PDF has {total_pages} pages (<= 20). Skipping split logic.")
        num_files = 1
        is_split = False
    else:
        num_files = (total_pages + pages_per_file - 1) // pages_per_file
        is_split = True
    
    progress_data = {
        "source_file": input_pdf_path,
        "final_filename": final_filename,
        "total_pages": total_pages,
        "is_split": is_split,
        "chunks": []
    }

    print(f"Total pages: {total_pages}")
    
    if not is_split:
        # For small files, just record the source file in progress.json
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
        print(f"Number of output files: {num_files}")
        for i in range(num_files):
            writer = PdfWriter()
            start_page = i * pages_per_file
            end_page = min(start_page + pages_per_file, total_pages)

            for page_num in range(start_page, end_page):
                writer.add_page(reader.pages[page_num])

            output_filename = f"{base_name}_part_{i + 1}_p{start_page + 1}-{end_page}.pdf"
            output_path = os.path.join(output_dir, output_filename)
            
            with open(output_path, "wb") as f_out:
                writer.write(f_out)

            progress_data["chunks"].append({
                "part": i + 1,
                "filename": output_filename,
                "start_page": start_page + 1,
                "end_page": end_page,
                "status": "pending",
                "output_file": ""
            })
            print(f"Created: {output_filename}")

    # Save the progress tracker
    progress_path = os.path.join(output_dir, "progress.json")
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=4, ensure_ascii=False)
    
    print(f"\nWorkflow Initialized: {progress_path} created.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split PDF and init workflow.")
    parser.add_argument("input_pdf", help="Path to the source PDF file.")
    parser.add_argument("--pages_per_file", type=int, default=20, help="Pages per chunk.")
    parser.add_argument("--info_only", action="store_true", help="Only show total pages.")
    args = parser.parse_args()

    if args.info_only:
        total = get_pdf_info(args.input_pdf)
        print(f"Total pages: {total}")
    else:
        split_pdf(args.input_pdf, "output_parts", args.pages_per_file)
