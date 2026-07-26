"""Analyze images embedded inside a PDF file to detect resolution, color mode, compression, and size footprint."""

import sys
from pathlib import Path
import fitz  # PyMuPDF


def analyze_pdf_images(pdf_path: str, max_pages: int = 30) -> None:
    path = Path(pdf_path)
    if not path.exists():
        print(f"Error: File not found at '{pdf_path}'", file=sys.stderr)
        return

    doc = fitz.open(str(path))
    total_pages = len(doc)
    pages_to_check = min(max_pages, total_pages)

    print("==================================================")
    print(f"PDF Image Analysis Report: {path.name}")
    print(f"Total Pages in PDF: {total_pages}")
    print(f"Pages Analyzed in Sample: 1 to {pages_to_check}")
    print("==================================================\n")

    total_image_bytes = 0
    image_count = 0
    color_spaces = {}
    compressions = {}
    resolutions = []
    page_sizes = []

    seen_xrefs = set()

    for page_index in range(pages_to_check):
        page = doc[page_index]
        image_list = page.get_images(full=True)
        page_img_bytes = 0

        for img in image_list:
            xref = img[0]
            width = img[2]
            height = img[3]
            bpc = img[4]
            colorspace = img[5]
            alt_cs = img[6]
            img_name = img[7]
            filter_name = img[8]

            # Extract raw image stream data size
            try:
                img_data = doc.extract_image(xref)
                img_bytes_size = len(img_data["image"])
                ext = img_data["ext"]
            except Exception:
                img_bytes_size = 0
                ext = filter_name or "unknown"

            if xref not in seen_xrefs:
                seen_xrefs.add(xref)
                image_count += 1
                total_image_bytes += img_bytes_size
                page_img_bytes += img_bytes_size

                color_spaces[colorspace] = color_spaces.get(colorspace, 0) + 1
                compressions[ext] = compressions.get(ext, 0) + 1
                resolutions.append((width, height))

            if page_index < 10:
                print(
                    f"Page {page_index + 1:2d} | xref: {xref:5d} | "
                    f"Dim: {width}x{height} px | ColorSpace: {colorspace} ({bpc}bpc) | "
                    f"Format: {ext.upper()} | Size: {img_bytes_size / 1024:.2f} KB"
                )

        page_sizes.append(page_img_bytes)

    avg_img_size_kb = (total_image_bytes / image_count / 1024) if image_count else 0
    sample_total_mb = total_image_bytes / (1024 * 1024)
    est_total_book_mb = (sample_total_mb / pages_to_check) * total_pages if pages_to_check else 0

    print("\n--------------------------------------------------")
    print("SUMMARY & STATISTICAL DIAGNOSIS")
    print("--------------------------------------------------")
    print(f"Unique Images Analyzed : {image_count}")
    print(f"Total Image Data (Sample 1-{pages_to_check}) : {sample_total_mb:.2f} MB")
    print(f"Average Image Size     : {avg_img_size_kb:.2f} KB / image")
    print(f"Estimated Book Size (Images) : ~{est_total_book_mb:.2f} MB")
    print("\nColor Spaces Distribution:")
    for cs, count in color_spaces.items():
        print(f"  - {cs}: {count} image(s)")
    print("\nCompression Formats Distribution:")
    for fmt, count in compressions.items():
        print(f"  - {fmt.upper()}: {count} image(s)")
    if resolutions:
        avg_w = sum(r[0] for r in resolutions) / len(resolutions)
        avg_h = sum(r[1] for r in resolutions) / len(resolutions)
        print(f"\nAverage Resolution     : {avg_w:.0f} x {avg_h:.0f} pixels")
    print("==================================================")


if __name__ == "__main__":
    pdf_file = sys.argv[1] if len(sys.argv) > 1 else "E:\\downloads\\ar_hidayat_alquran_alkarim.pdf"
    max_p = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    analyze_pdf_images(pdf_file, max_p)
