"""Generate fake-data sample PDFs used by the tests.

The inputs are blank public payer forms in examples/blank. Outputs contain only
synthetic test data and must never be replaced with real patient information.
"""
from __future__ import annotations

import io
import random
import textwrap
from pathlib import Path

import pymupdf
from PIL import Image, ImageEnhance, ImageFilter
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
BLANK = ROOT / "examples" / "blank" / "indiana-ihcp-pa-form.pdf"
OUT_DIR = ROOT / "examples" / "generated"
CLEAN_OUT = OUT_DIR / "indiana-ihcp-test-filled.pdf"
FAX_OUT = OUT_DIR / "indiana-ihcp-test-fax.pdf"

FAKE_DATA = {
    "Patient IHCP Member ID": "TESTM123456789",
    "Patient Date of Birth": "01/15/1980",
    "Patient Name": "TEST PATIENT",
    "Patient Address": "123 Example Ave",
    "Patient City State ZIP Code": "Indianapolis, IN 46204",
    "Patient Guardian Phone": "555-0100",
    "Requesting Provider NPI": "1234567893",
    "Requesting Provider Name": "TEST PROVIDER MD",
    "Dx1": "M54.50",
    "Start Date of Service Row1": "03/01/2026",
    "Stop Date of Service Row1": "03/14/2026",
    "Procedure/Service Code Row1": "99213",
    "Service Description Row 1": "Office visit",
    "Notes": "FAKE TEST DATA ONLY. Low back pain persists after conservative therapy. Requesting office visit authorization for synthetic fixture validation.",
    "Date": "02/20/2026",
}


def field_rects(reader: PdfReader) -> dict[str, tuple[int, list[float]]]:
    rects = {}
    for page_index, page in enumerate(reader.pages):
        for annot_ref in page.get("/Annots", []):
            annot = annot_ref.get_object()
            name = annot.get("/T")
            if name in FAKE_DATA and name not in rects:
                rects[name] = (page_index, [float(v) for v in annot["/Rect"]])
    missing = sorted(set(FAKE_DATA) - set(rects))
    if missing:
        raise RuntimeError(f"missing form fields: {missing}")
    return rects


def make_overlay(reader: PdfReader) -> PdfReader:
    packet = io.BytesIO()
    first_page = reader.pages[0]
    width = float(first_page.mediabox.width)
    height = float(first_page.mediabox.height)
    c = canvas.Canvas(packet, pagesize=(width, height))
    rects = field_rects(reader)

    for page_index in range(len(reader.pages)):
        for name, value in FAKE_DATA.items():
            field_page, rect = rects[name]
            if field_page != page_index:
                continue
            x0, y0, x1, y1 = rect
            c.setFont("Helvetica", 7.2)
            if name == "Notes":
                lines = textwrap.wrap(value, width=92)
                y = y1 - 16
                for line in lines[:8]:
                    c.drawString(x0 + 4, y, line)
                    y -= 10
            else:
                c.drawString(x0 + 2, y0 + 4, value)
        c.showPage()
    c.save()
    packet.seek(0)
    return PdfReader(packet)


def write_clean() -> None:
    reader = PdfReader(str(BLANK))
    overlay = make_overlay(reader)
    writer = PdfWriter(clone_from=str(BLANK))
    for page, overlay_page in zip(writer.pages, overlay.pages, strict=True):
        page.merge_page(overlay_page)
    with CLEAN_OUT.open("wb") as f:
        writer.write(f)


def write_fax() -> None:
    random.seed(7)
    source = pymupdf.open(CLEAN_OUT)
    output = pymupdf.open()
    zoom = 300 / 72
    for page in source:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        image = image.rotate(-0.4, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=245)
        image = ImageEnhance.Contrast(image).enhance(0.95)
        pixels = image.load()
        for _ in range(image.width * image.height // 2500):
            x = random.randrange(image.width)
            y = random.randrange(image.height)
            pixels[x, y] = random.randrange(125, 225)
        image = image.filter(ImageFilter.GaussianBlur(0.05))
        encoded = io.BytesIO()
        image.save(encoded, format="PNG", optimize=True)
        out_page = output.new_page(width=612, height=792)
        out_page.insert_image(fitz_rect(), stream=encoded.getvalue())
    output.save(FAX_OUT, deflate=True)


def fitz_rect() -> pymupdf.Rect:
    return pymupdf.Rect(0, 0, 612, 792)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_clean()
    write_fax()
    print(CLEAN_OUT)
    print(FAX_OUT)


if __name__ == "__main__":
    main()
