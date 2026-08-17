import pymupdf
import pytest

from senthire.extraction.pdf import EncryptedPdfError, analyze_pdf


def make_pdf(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for content in pages:
        page = doc.new_page()
        if content:
            page.insert_text((72, 72), content)
    data = doc.tobytes()
    doc.close()
    return data


def test_text_layer_detected_and_linearized():
    # Fixture uses Latin-1-safe Turkish chars (ç/ö/ü): PyMuPDF's base-14 Helvetica
    # can't *write* ş/ı when creating the fixture. Real CVs embed full fonts;
    # extraction of those is covered by the golden-set harness, not this unit test.
    text = "Çözüm Müdürü — Acme Yazilim A.S. 2019-2024\nAnkara, Türkiye"
    analysis = analyze_pdf(make_pdf([text, "Second page with enough characters to count here."]))
    assert analysis.page_count == 2
    assert analysis.has_text_layer is True
    assert "=== Page 1 ===" in analysis.text
    assert "Çözüm Müdürü" in analysis.text
    assert "Türkiye" in analysis.text


def test_blank_pdf_routes_to_vision_path():
    analysis = analyze_pdf(make_pdf(["", ""]))
    assert analysis.page_count == 2
    assert analysis.has_text_layer is False
    assert analysis.text == ""


def test_encrypted_pdf_raises():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    with pytest.raises(EncryptedPdfError):
        analyze_pdf(data)
