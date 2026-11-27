# prior-auth-intake

A small Python 3.11 proof of concept that extracts structured JSON from a prior authorization PDF and flags incomplete or invalid intake data for human review.

It handles two input shapes:

- clean, text-based PDFs with `pdfplumber`
- rasterized/rotated fax-like PDFs with Tesseract OCR

All bundled filled examples use synthetic data only. Do not place real patient information or PHI in this repository.

## Forms used

Blank public payer form in `examples/blank/`:

- [Indiana Health Coverage Programs Universal Prior Authorization Request Form](https://www.in.gov/medicaid/providers/files/pa-form.pdf) — used for the filled clean and fax-style samples because it contains patient, member, NPI, ICD-10, procedure, service-date, and notes fields.

## Install

System requirement: Python 3.11 plus the Tesseract OCR binary.

```bash
# Ubuntu/Debian
sudo apt-get install tesseract-ocr

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
python parse.py examples/generated/indiana-ihcp-test-filled.pdf
python parse.py examples/generated/indiana-ihcp-test-fax.pdf
```

Output is JSON with:

- `patient` — name, date of birth, address, phone
- `member_id`
- `provider_npi` and `provider_name`
- `cpt_hcpcs`
- `icd10`
- `dates` — service start/end and signature date
- `clinical_notes`
- `escalate_to_human` — field-level reasons for review

Example escalation item:

```json
{
  "field": "provider_npi",
  "reason": "invalid NPI checksum"
}
```

Validation currently checks required-field presence, NPI length/checksum, ICD-10 shape, CPT/HCPCS shape, date format, service-date ordering, and sparse clinical notes.

## Tests

```bash
.venv/bin/pytest -q
```

The tests cover both bundled samples: the clean digitally filled PDF and the rasterized, slightly rotated fax-style PDF. They also cover blank-form escalation and format validators.

## Regenerating samples

```bash
.venv/bin/python scripts/generate_samples.py
```

This overlays synthetic fixture data onto the blank Indiana form, then creates a grayscale, slightly rotated, noisy raster PDF to mimic a fax. The fake data is intentionally labeled `FAKE TEST DATA ONLY`.

## Where an agent acts vs where a human steps in

An agent can safely act on deterministic intake work:

- classify whether the PDF is digital or scanned
- extract candidate values from a reviewed payer-form layout
- normalize dates and identifiers
- validate presence and formats
- produce a structured work item and escalation reasons
- route complete, internally consistent packets for the next non-clinical processing step

A human must step in when the output contains anything in `escalate_to_human`, including:

- missing member, patient, provider, procedure, diagnosis, date, or clinical-note data
- an invalid or OCR-uncertain NPI, ICD-10, CPT/HCPCS, or date
- contradictory service dates or sparse medical-necessity documentation
- low-quality scans where OCR confidence or visual verification is inadequate
- any clinical judgment, medical-necessity decision, benefit determination, or patient/provider communication

This tool is an intake assistant, not an authorization decision system. OCR output should be treated as untrusted until validated or reviewed.

## Layout scope

Payer PDFs are visually similar but not semantically tagged. This MVP uses reviewed field coordinates for the Indiana IHCP universal form. Supporting another payer template means adding a small adapter with that template's coordinates and regression samples; generic coordinate-free extraction is deliberately out of scope for this small tool.
