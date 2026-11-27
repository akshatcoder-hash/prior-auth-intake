#!/usr/bin/env python3
"""Extract structured intake JSON from a prior authorization PDF."""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pdfplumber
import pymupdf
import pytesseract
from PIL import Image, ImageOps

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

# Coordinates are PDF points in the Indiana IHCP Universal PA form's native
# bottom-left coordinate space. This project intentionally uses a layout adapter:
# payer forms are not semantically tagged, so production systems normally need
# one reviewed adapter per high-volume payer/template.
FIELDS = {
    "member_id": (0, (115.5, 485.7, 295.1, 500.8), "member"),
    "patient_dob": (0, (98.2, 468.7, 295.1, 483.8), "date"),
    "patient_name": (0, (98.7, 451.6, 295.1, 466.8), "text"),
    "patient_address": (0, (79.1, 434.6, 295.1, 449.8), "text"),
    "patient_city_state_zip": (0, (124.7, 417.5, 295.1, 432.7), "text"),
    "patient_phone": (0, (140.3, 400.4, 295.1, 415.5), "phone"),
    "provider_npi": (0, (479.8, 485.7, 583.1, 500.9), "npi"),
    "provider_name": (0, (393.6, 434.6, 583.1, 449.7), "text"),
    "icd10_primary": (0, (69.1, 260.8, 123.5, 276.1), "icd"),
    "service_start": (0, (36.5, 119.4, 73.9, 133.8), "date"),
    "service_end": (0, (75.5, 119.4, 112.9, 133.8), "date"),
    "cpt_hcpcs": (0, (114.4, 119.4, 166.2, 133.8), "procedure"),
    "service_description": (0, (276.2, 119.4, 377.9, 133.8), "text"),
    "clinical_notes": (1, (32.6, 233.0, 573.3, 737.9), "multiline"),
    "signature_date": (1, (463.8, 144.0, 540.0, 167.5), "date"),
}

OCR_FIELDS = dict(FIELDS)
# OCR only needs the populated top of the large notes box; the full box is
# mostly empty and slower/noisier to recognize.
OCR_FIELDS["clinical_notes"] = (1, (32.6, 600.0, 573.3, 737.9), "multiline")

DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
NPI_RE = re.compile(r"\b\d{10}\b")
PROCEDURE_RE = re.compile(r"\b(?:\d{5}|[A-Z]\d{4})\b", re.IGNORECASE)
ICD_RE = re.compile(r"\b[A-TV-Z]\d{2}(?:\.?[A-Z0-9]{1,4})?\b", re.IGNORECASE)


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def canonical_icd(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9.]", "", value).upper()
    if len(value) >= 5 and "." not in value:
        value = value[:3] + "." + value[3:]
    return value


def normalize_value(raw: str, kind: str) -> str:
    text = clean_spaces(raw).strip(":;| ")
    if kind == "date":
        compact = re.sub(r"\s+", "", text)
        match = DATE_RE.search(compact)
        return match.group(0) if match else ""
    if kind == "npi":
        digits = re.sub(r"\D", "", text)
        match = re.search(r"\d{10}", digits)
        return match.group(0) if match else ""
    if kind == "procedure":
        match = PROCEDURE_RE.search(text)
        return match.group(0).upper() if match else ""
    if kind == "icd":
        match = ICD_RE.search(text.replace(" ", ""))
        return canonical_icd(match.group(0)) if match else ""
    if kind == "member":
        compact = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        match = re.search(r"[A-Z0-9]{6,}", compact)
        return match.group(0) if match else ""
    if kind == "phone":
        match = re.search(r"\d{3}[- ]?\d{4}", text)
        return match.group(0) if match else text
    return text


def crop_box_pdfplumber(page, box):
    x0, y0, x1, y1 = box
    top0 = page.height - y1
    top1 = page.height - y0
    return page.crop((x0, top0, x1, top1))


def extract_digital(path: Path) -> dict[str, str]:
    values = {}
    with pdfplumber.open(path) as pdf:
        for key, (page_index, box, kind) in FIELDS.items():
            if page_index >= len(pdf.pages):
                values[key] = ""
                continue
            crop = crop_box_pdfplumber(pdf.pages[page_index], box)
            values[key] = normalize_value(crop.extract_text() or "", kind)
    return values


def render_pages(path: Path, dpi: int = 300):
    doc = pymupdf.open(path)
    zoom = dpi / 72
    pages = []
    for page in doc:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        pages.append(image)
    return pages


def crop_image(image: Image.Image, box, pad: float = 0) -> Image.Image:
    x0, y0, x1, y1 = box
    sx = image.width / PAGE_WIDTH
    sy = image.height / PAGE_HEIGHT
    left = max(0, int((x0 - pad) * sx))
    right = min(image.width, int((x1 + pad) * sx))
    top = max(0, int((PAGE_HEIGHT - y1 - pad) * sy))
    bottom = min(image.height, int((PAGE_HEIGHT - y0 + pad) * sy))
    crop = image.crop((left, top, right, bottom))
    return ImageOps.autocontrast(crop)


def ocr_attempts(crop: Image.Image, kind: str) -> list[str]:
    if kind == "multiline":
        variants = [(crop, 6)]
    elif kind in {"date", "npi", "procedure", "member", "icd"}:
        scaled = crop.resize((crop.width * 3, crop.height * 3))
        variants = [(scaled, 8), (crop.resize((crop.width * 2, crop.height * 2)), 7)]
    else:
        variants = [(crop.resize((crop.width * 2, crop.height * 2)), 7)]
    output = []
    for image, psm in variants:
        text = pytesseract.image_to_string(image, config=f"--psm {psm}")
        output.append(clean_spaces(text))
    return output


def extract_ocr(path: Path) -> dict[str, str]:
    pages = render_pages(path)
    values = {}
    for key, (page_index, box, kind) in OCR_FIELDS.items():
        if page_index >= len(pages):
            values[key] = ""
            continue
        pad = 2 if kind not in {"date", "npi", "procedure", "member", "icd"} else 0
        crops = [crop_image(pages[page_index], box, pad=pad)]
        if pad == 0:
            crops.append(crop_image(pages[page_index], box, pad=2))
        candidates = []
        for crop in crops:
            candidates.extend(normalize_value(text, kind) for text in ocr_attempts(crop, kind))
        candidates = [candidate for candidate in candidates if candidate]
        if kind == "npi":
            values[key] = next((c for c in candidates if valid_npi(c)), candidates[0] if candidates else "")
        elif kind == "multiline":
            values[key] = max(candidates, key=len, default="")
        else:
            values[key] = candidates[0] if candidates else ""
    return values


def valid_npi(npi: str) -> bool:
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    digits = "80840" + npi[:9]
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return npi[-1] == str((10 - total % 10) % 10)


def valid_icd10(code: str) -> bool:
    return bool(re.fullmatch(r"[A-TV-Z]\d{2}(?:\.[A-Z0-9]{1,4}|\d{1,4})?", code or "", re.IGNORECASE))


def parse_date(value: str):
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None


def validate(data: dict) -> list[dict[str, str]]:
    issues = []

    def add(field: str, reason: str):
        issues.append({"field": field, "reason": reason})

    required = {
        "patient.name": data["patient"]["name"],
        "patient.date_of_birth": data["patient"]["date_of_birth"],
        "member_id": data["member_id"],
        "provider_npi": data["provider_npi"],
        "cpt_hcpcs": data["cpt_hcpcs"],
        "icd10.primary": data["icd10"][0] if data["icd10"] else "",
        "dates.service_start": data["dates"]["service_start"],
        "dates.service_end": data["dates"]["service_end"],
        "clinical_notes": data["clinical_notes"],
    }
    for field, value in required.items():
        if not value:
            add(field, "missing required field")

    npi = data["provider_npi"]
    if npi and not re.fullmatch(r"\d{10}", npi):
        add("provider_npi", "invalid NPI format; expected 10 digits")
    elif npi and not valid_npi(npi):
        add("provider_npi", "invalid NPI checksum")

    for code in data["icd10"]:
        if not valid_icd10(code):
            add("icd10", f"invalid ICD-10 format: {code}")

    if data["cpt_hcpcs"] and not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4})", data["cpt_hcpcs"], re.IGNORECASE):
        add("cpt_hcpcs", "invalid CPT/HCPCS format")

    parsed_dates = {}
    for field in ("date_of_birth",):
        parsed_dates[field] = parse_date(data["patient"][field])
        if data["patient"][field] and not parsed_dates[field]:
            add(f"patient.{field}", "invalid date format; expected MM/DD/YYYY")
    for field in ("service_start", "service_end", "signature_date"):
        parsed_dates[field] = parse_date(data["dates"][field])
        if data["dates"][field] and not parsed_dates[field]:
            add(f"dates.{field}", "invalid date format; expected MM/DD/YYYY")
    if parsed_dates.get("service_start") and parsed_dates.get("service_end"):
        if parsed_dates["service_end"] < parsed_dates["service_start"]:
            add("dates.service_end", "service end date is before service start date")

    if data["clinical_notes"] and len(data["clinical_notes"]) < 20:
        add("clinical_notes", "clinical notes are too sparse for medical-necessity review")
    return issues


def build_result(path: Path, method: str, values: dict[str, str]) -> dict:
    icd_values = [values["icd10_primary"]] if values["icd10_primary"] else []
    result = {
        "source_file": path.name,
        "template": "indiana_ihcp_universal_prior_authorization",
        "extraction_method": method,
        "patient": {
            "name": values["patient_name"],
            "date_of_birth": values["patient_dob"],
            "address": values["patient_address"],
            "city_state_zip": values["patient_city_state_zip"],
            "phone": values["patient_phone"],
        },
        "member_id": values["member_id"],
        "provider_npi": values["provider_npi"],
        "provider_name": values["provider_name"],
        "cpt_hcpcs": values["cpt_hcpcs"],
        "icd10": icd_values,
        "dates": {
            "service_start": values["service_start"],
            "service_end": values["service_end"],
            "signature_date": values["signature_date"],
        },
        "service_description": values["service_description"],
        "clinical_notes": values["clinical_notes"],
        "escalate_to_human": [],
    }
    result["escalate_to_human"] = validate(result)
    return result


def is_digital_pdf(path: Path) -> bool:
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages[:1])
        return len(text) > 300
    except Exception:
        return False


def parse_form(path: str | Path) -> dict:
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    if is_digital_pdf(pdf_path):
        return build_result(pdf_path, "pdfplumber", extract_digital(pdf_path))
    return build_result(pdf_path, "tesseract_ocr", extract_ocr(pdf_path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse a prior authorization PDF into structured JSON.")
    parser.add_argument("form", help="Path to a prior authorization PDF")
    args = parser.parse_args(argv)
    try:
        result = parse_form(args.form)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
