import re
from datetime import datetime
from pathlib import Path

import pikepdf
import pytest
from fpdf.enums import DocumentCompliance
from pypdf import PdfReader

from paperlessngx_default_parser.content_listers import ArchiveEntry
from paperlessngx_default_parser.pdf_renderer import FpdfRenderer

# Liberation fonts are used in the Paperless-ngx image, the others allow running the tests elsewhere
FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
]
FONTS = next(
    ((Path(sans), Path(mono)) for sans, mono in FONT_CANDIDATES if Path(sans).is_file() and Path(mono).is_file()),
    None,
)
pytestmark = pytest.mark.skipif(FONTS is None, reason="no unicode TrueType font found")


@pytest.fixture
def renderer() -> FpdfRenderer:
    return FpdfRenderer(*FONTS, compliance=DocumentCompliance.PDFA_2B)


def text_of(path: Path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def entries(count: int, name: str = "fotos/IMG_{:05d}.jpg") -> list[ArchiveEntry]:
    return [ArchiveEntry(name.format(i), 1024, datetime(2024, 1, 2, 3, 4)) for i in range(count)]


def rendered_and_omitted(text: str) -> tuple[int, int]:
    omitted = re.search(r"(\d+) more entries are not shown", text)
    return text.count("IMG_"), int(omitted.group(1)) if omitted else 0


def test_listing_without_limit_shows_all_entries(renderer, tmp_path):
    listing = [
        ArchiveEntry("Steuer 2023/", None, None, is_dir=True),
        ArchiveEntry("Steuer 2023/Antrag auf schlichte Änderung.rtf", 12, datetime(2020, 5, 17, 10, 30)),
        ArchiveEntry("Steuer 2023/Квитанция <Verein & Co>.pdf", 2048, None),
    ]

    path = renderer.listing(tmp_path / "out.pdf", "Steuer 2023.zip", "application/zip", listing)
    text = text_of(path)

    assert len(PdfReader(path).pages) == 1
    assert "Steuer 2023.zip" in text
    assert "2 files, 1 directories, 2.0 KB uncompressed" in text
    assert "Antrag auf schlichte Änderung.rtf" in text
    assert "Квитанция <Verein & Co>.pdf" in text
    assert "2020-05-17 10:30" in text
    assert "not shown" not in text


@pytest.mark.parametrize("max_pages", [1, 3])
def test_listing_respects_max_pages(renderer, tmp_path, max_pages):
    path = renderer.listing(tmp_path / "out.pdf", "a.zip", "application/zip", entries(6000), max_pages=max_pages)

    assert len(PdfReader(path).pages) == max_pages
    rendered, omitted = rendered_and_omitted(text_of(path))
    assert rendered > 0
    assert rendered + omitted == 6000


def test_listing_respects_max_pages_with_wrapping_names(renderer, tmp_path):
    long_name = "verzeichnis/" + "sehr-langer-dateiname-" * 12 + "IMG_{:05d}.jpg"

    path = renderer.listing(tmp_path / "out.pdf", "a.zip", "application/zip", entries(500, long_name), max_pages=3)

    assert len(PdfReader(path).pages) == 3
    rendered, omitted = rendered_and_omitted(text_of(path))
    assert rendered + omitted == 500


def test_listing_fitting_into_max_pages_has_no_note(renderer, tmp_path):
    path = renderer.listing(tmp_path / "out.pdf", "a.zip", "application/zip", entries(20), max_pages=3)

    assert len(PdfReader(path).pages) == 1
    assert rendered_and_omitted(text_of(path)) == (20, 0)


def test_listing_respects_max_entries(renderer, tmp_path):
    path = renderer.listing(tmp_path / "out.pdf", "a.zip", "application/zip", entries(300), max_entries=100)

    assert rendered_and_omitted(text_of(path)) == (100, 200)


def test_note_and_text(renderer, tmp_path):
    note = renderer.note(tmp_path / "note.pdf", "kaputt.zip", "application/zip")
    text = renderer.text(tmp_path / "text.pdf", "Zeile 1: Übersicht\nСтрока 2\n" * 200)

    assert "original file name: kaputt.zip" in text_of(note)
    assert len(PdfReader(text).pages) > 1
    assert "Zeile 1: Übersicht" in text_of(text)
    assert "Строка 2" in text_of(text)


def test_pdfa_compliance(renderer, tmp_path):
    path = renderer.note(tmp_path / "note.pdf", "a.bin", "application/octet-stream")

    with pikepdf.open(path) as pdf:
        assert pdf.open_metadata().pdfa_status == "2B"


def test_without_compliance(tmp_path):
    path = FpdfRenderer(*FONTS).note(tmp_path / "note.pdf", "a.bin", "application/octet-stream")

    with pikepdf.open(path) as pdf:
        assert pdf.open_metadata().pdfa_status == ""
