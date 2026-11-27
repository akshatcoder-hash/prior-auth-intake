from pathlib import Path

from parse import parse_form, valid_icd10, valid_npi

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "examples" / "generated" / "indiana-ihcp-test-filled.pdf"
FAX = ROOT / "examples" / "generated" / "indiana-ihcp-test-fax.pdf"
BLANK = ROOT / "examples" / "blank" / "indiana-ihcp-pa-form.pdf"


def assert_core_fields(result):
    assert result["patient"]["name"] == "TEST PATIENT"
    assert result["patient"]["date_of_birth"] == "01/15/1980"
    assert result["member_id"] == "TESTM123456789"
    assert result["provider_npi"] == "1234567893"
    assert result["cpt_hcpcs"] == "99213"
    assert result["icd10"] == ["M54.50"]
    assert result["dates"]["service_start"] == "03/01/2026"
    assert result["dates"]["service_end"] == "03/14/2026"
    assert "FAKE TEST DATA ONLY" in result["clinical_notes"]
    assert result["escalate_to_human"] == []


def test_clean_digital_pdf_extracts_schema():
    result = parse_form(CLEAN)
    assert result["extraction_method"] == "pdfplumber"
    assert_core_fields(result)


def test_rotated_raster_fax_extracts_schema_with_ocr():
    result = parse_form(FAX)
    assert result["extraction_method"] == "tesseract_ocr"
    assert_core_fields(result)


def test_blank_form_escalates_missing_required_fields():
    result = parse_form(BLANK)
    fields = {issue["field"] for issue in result["escalate_to_human"]}
    assert "patient.name" in fields
    assert "member_id" in fields
    assert "provider_npi" in fields
    assert "clinical_notes" in fields


def test_format_checkers():
    assert valid_npi("1234567893")
    assert not valid_npi("1234567890")
    assert valid_icd10("M54.50")
    assert valid_icd10("E119")
    assert not valid_icd10("954.50")
