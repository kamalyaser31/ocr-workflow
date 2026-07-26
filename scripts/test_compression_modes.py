"""Compare DPI and compression formats to find the optimal size-to-quality threshold."""

import sys
from pathlib import Path
import fitz  # PyMuPDF
import img2pdf


def test_dpi_levels(pdf_path: str, pages_count: int = 20) -> None:
    doc = fitz.open(pdf_path)

    print("==================================================")
    print("OPTIMAL RESOLUTION & SIZE COMPARISON REPORT")
    print("==================================================")

    for dpi in [150, 200]:
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        jpg_bytes_list = []

        for i in range(pages_count):
            page = doc[i]
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
            jpg_bytes = pix.tobytes("jpeg", jpg_quality=75)
            jpg_bytes_list.append(jpg_bytes)

        jpg_pdf = img2pdf.convert(jpg_bytes_list)
        jpg_size_mb = len(jpg_pdf) / (1024 * 1024)
        est_full_book_mb = (jpg_size_mb / pages_count) * len(doc)

        print(f"DPI: {dpi:3d} | Sample (20 p): {jpg_size_mb:.2f} MB | Est. Full Book: {est_full_book_mb:.2f} MB")

    print("==================================================")


if __name__ == "__main__":
    pdf_in = sys.argv[1] if len(sys.argv) > 1 else "E:\\downloads\\ar_hidayat_alquran_alkarim.pdf"
    test_dpi_levels(pdf_in, 20)
