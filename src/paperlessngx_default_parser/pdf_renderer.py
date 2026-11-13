"""
Renders the PDFs of the default parser with fpdf2, as an alternative to
Gotenberg for systems where Chromium can't run.
"""

from pathlib import Path

from fpdf import FPDF
from fpdf.enums import DocumentCompliance, MethodReturnValue, XPos, YPos

from paperlessngx_default_parser.content_listers import (
    ArchiveEntry,
    describe,
    format_size,
)

FONT_SIZE = 9
LINE_HEIGHT = 4.5
MARGIN = 15
SIZE_WIDTH = 22
MODIFIED_WIDTH = 30


class FpdfRenderer:
    def __init__(
        self,
        font_path: Path,
        mono_font_path: Path,
        compliance: DocumentCompliance | None = None,
    ) -> None:
        self.font_path = font_path
        self.mono_font_path = mono_font_path
        self.compliance = compliance

    def _new_pdf(self) -> FPDF:
        pdf = FPDF(format="A4", enforce_compliance=self.compliance)
        pdf.set_margins(MARGIN, MARGIN, MARGIN)
        pdf.set_auto_page_break(True, margin=MARGIN)
        pdf.add_font("sans", "", self.font_path)
        pdf.add_font("mono", "", self.mono_font_path)
        pdf.set_font("sans", size=FONT_SIZE)
        return pdf

    def _write_lines(self, pdf: FPDF, *lines: str) -> None:
        for line in lines:
            pdf.multi_cell(0, LINE_HEIGHT, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def note(self, out_path: Path, file_name: str, mime_type: str) -> Path:
        pdf = self._new_pdf()
        pdf.add_page()
        self._write_lines(
            pdf,
            "This document was archived by a default parser for Paperless-ngx.",
            "",
            f"original file name: {file_name}",
            f"mime type: {mime_type}",
            "",
            "Download original file to work with it.",
        )
        pdf.output(str(out_path))
        return out_path

    def text(self, out_path: Path, text: str) -> Path:
        pdf = self._new_pdf()
        pdf.add_page()
        pdf.set_font("mono", size=FONT_SIZE)
        pdf.multi_cell(0, LINE_HEIGHT, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.output(str(out_path))
        return out_path

    def listing(
        self,
        out_path: Path,
        file_name: str,
        mime_type: str,
        entries: list[ArchiveEntry],
        max_pages: int = 0,
        max_entries: int | None = None,
    ) -> Path:
        """
        Renders a table of the entries. With max_pages > 0 the PDF has at most
        that many pages, with max_entries at most that many rows. If entries
        are left out, the PDF ends with a note about them.
        """
        pdf = self._new_pdf()
        pdf.add_page()
        # rows are placed manually, so page breaks can respect max_pages
        pdf.set_auto_page_break(False, margin=MARGIN)
        name_width = pdf.epw - SIZE_WIDTH - MODIFIED_WIDTH
        note_height = 2 * LINE_HEIGHT

        self._write_lines(
            pdf,
            f"original file name: {file_name}",
            f"mime type: {mime_type}",
            f"entries: {describe(entries)}",
            "",
        )

        def heading() -> None:
            pdf.cell(name_width, LINE_HEIGHT, "name")
            pdf.cell(SIZE_WIDTH, LINE_HEIGHT, "size", align="R")
            pdf.cell(MODIFIED_WIDTH, LINE_HEIGHT, "  modified", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self._separator(pdf)

        heading()
        omitted = 0
        for index, entry in enumerate(entries):
            if max_entries is not None and index >= max_entries:
                omitted = len(entries) - index
                break
            lines = pdf.multi_cell(
                name_width,
                LINE_HEIGHT,
                entry.name,
                dry_run=True,
                output=MethodReturnValue.LINES,
            )
            row_height = len(lines) * LINE_HEIGHT
            on_last_page = max_pages > 0 and pdf.page_no() >= max_pages
            is_last_entry = index == len(entries) - 1
            reserved = note_height if on_last_page and not is_last_entry else 0
            if pdf.get_y() + row_height > pdf.page_break_trigger - reserved:
                if on_last_page:
                    omitted = len(entries) - index
                    break
                pdf.add_page()
                heading()

            top = pdf.get_y()
            pdf.multi_cell(name_width, LINE_HEIGHT, entry.name, new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.cell(SIZE_WIDTH, LINE_HEIGHT, format_size(entry.size), align="R")
            pdf.cell(
                MODIFIED_WIDTH,
                LINE_HEIGHT,
                "  " + entry.modified.strftime("%Y-%m-%d %H:%M") if entry.modified else "",
            )
            pdf.set_xy(pdf.l_margin, top + row_height)
            self._separator(pdf)

        if omitted:
            # only possible if max_entries stopped on a full page, the last allowed page has the space reserved
            if pdf.get_y() + note_height > pdf.page_break_trigger:
                pdf.add_page()
            self._write_lines(
                pdf,
                f"{omitted} more entries are not shown here, but are included in the document's content.",
            )
        elif pdf.get_y() + 2 * LINE_HEIGHT <= pdf.page_break_trigger:
            self._write_lines(pdf, "", "Download original file to work with it.")

        pdf.output(str(out_path))
        return out_path

    @staticmethod
    def _separator(pdf: FPDF) -> None:
        pdf.set_draw_color(220)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
