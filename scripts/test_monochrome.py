"""Demonstrate 1-bit Monochrome 300 DPI thresholding for ultra-compact high-clarity PDF."""

import sys
from pathlib import Path
import fitz  # PyMuPDF
from PIL import Image
import io
import img2pdf


def test_monochrome_300dpi(pdf_path: str, pages_count: int = 20) -> None:
    doc = fitz.open(pdf_path)
    dpi = 300
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    mono_png_bytes = []

    for i in range(pages_count):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)

        # Convert 8-bit Grayscale to 1-bit Monochrome (Strict Black & White)
        img_gray = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        img_mono = img_gray.convert("1", dither=Image.NONE)

        buf = io.BytesIO()
        img_mono.save(buf, format="PNG", optimize=True)
        mono_png_bytes.append(buf.getvalue())

    pdf_bytes = img2pdf.convert(mono_png_bytes)
    sample_mb = len(pdf_bytes) / (1024 * 1024)
    est_full_mb = (sample_mb / pages_count) * len(doc)

    print("==================================================")
    print("1-BIT MONOCHROME @ 300 DPI RESULT REPORT")
    print("==================================================")
    print(f"Sample 20 pages (1-bit 300 DPI) : {sample_mb:.2f} MB")
    print(f"Estimated Full Book (623 pages) : ~{est_full_mb:.2f} MB")
    print("==================================================")


if __name__ == "__main__":
    pdf_in = sys.argv[1] if len(sys.argv) > 1 else "E:\\downloads\\ar_hidayat_alquran_alkarim.pdf"
    test_monochrome_300dpi(pdf_in, 20)
