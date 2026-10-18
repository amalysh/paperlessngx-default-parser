import os
import shutil
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from types import TracebackType
from typing import Self

from chardet.universaldetector import UniversalDetector
from django.conf import settings
from documents.parsers import DocumentParser, ParseError
from gotenberg_client import GotenbergClient
from gotenberg_client.options import PdfAFormat
from paperless.models import OutputTypeChoices
from PIL import Image, ImageDraw, ImageFont

from paperless.parsers import ParserContext

from paperlessngx_default_parser.content_listers import CONTENT_LISTERS, ArchiveEntry


class DefaultDocumentParser(DocumentParser):
    """
    This parser can be used to archive documents of arbitrary mime types with Paperless-ngx version 3.0.0 and above.
    If it parses a container format with a registered content lister (e.g. a ZIP archive) the names of the contained
    entries are stored in the document's content metadata and a PDF listing them is generated.
    If it parses a document with known encoding the content of the file is stored in the document's content metadata and a PDF with its
    content is generated.
    Otherwise the content metadata is left empty and a pdf containing a note regarding the use of this parser is generated.
    """

    name = "Paperless-ngx default parser"
    version = "3.0.1"
    author = "toweosp"
    url = "https://github.com/toweosp/paperlessngx-default-parser"

    logging_name = "paperless.parsing.paperlessngx_default_parser"

    html_note = Template(
        """
    <HTML>
    This document was archived by a default parser for Paperless-ngx. 
    <p>
    <b>original file name:</b> $file_name<br>
    <b>mime type:</b> $mime_type<br>
    <p>
    Download original file to work with it.
    </HTML>
    """
    )

    note = Template(
        """
    This document was archived by a default parser for Paperless-ngx. 

    original file name: $file_name
    mime type: $mime_type

    Download original file to work with it.
    """
    )

    # Limits the rows of the generated PDF, the document's content always lists all entries
    max_rendered_entries = 5000

    def __init__(self, logging_group: object = None) -> None:
        # sets self.log, which DocumentParser.__init__ would do
        self.renew_logging_group()
        settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        self._tempdir = Path(
            tempfile.mkdtemp(prefix="paperless-", dir=settings.SCRATCH_DIR)
        )
        self._text: str = ""
        self._archive_path: Path | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        shutil.rmtree(self._tempdir, ignore_errors=True)

    def configure(self, context: ParserContext) -> None:
        pass

    @classmethod
    def supported_mime_types(cls) -> dict[str, str]:
        """
        This parser handles documents of all mime types listed in /etc/mime.types
        """
        mimetype_defaultextension_map = {}

        with open("/etc/mime.types") as f:
            while input := f.readline():
                input = input.rstrip()
                if not input.startswith("#") and input.strip():
                    mimetype, *other_columns = input.split("\t")
                    extensions_column = list(filter(None, other_columns))

                    default_extension = (
                        "." + extensions_column[0].split(" ")[0]
                        if extensions_column
                        else ""
                    )

                    mimetype_defaultextension_map[mimetype] = default_extension
            f.close()

        # ensure formats with a content lister are handled even if missing in /etc/mime.types
        for mimetype, lister in CONTENT_LISTERS.items():
            mimetype_defaultextension_map.setdefault(mimetype, lister.extension)

        return mimetype_defaultextension_map

    @classmethod
    def score(
        cls,
        mime_type: str,
        filename: str,
        path: "Path | None" = None,
    ) -> int | None:
        """Only handle file if no other parser is suitable."""
        return 0

    @property
    def can_produce_archive(self) -> bool:
        return False

    @property
    def requires_pdf_rendition(self) -> bool:
        return True

    def get_thumbnail(self, document_path: Path, mime_type: str):
        img = Image.new("RGB", (500, 700), color="white")
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(
            font=settings.THUMBNAIL_FONT_NAME,
            size=20,
            layout_engine=ImageFont.Layout.BASIC,
        )
        thumbnail_text = (
            self._text
            if self._text
            else self.note.substitute(mime_type=mime_type, file_name=str(document_path.name))
        )
        draw.text((5, 5), thumbnail_text, font=font, fill="black")

        out_path = os.path.join(self._tempdir, "thumb.webp")
        img.save(out_path, format="WEBP")
        return out_path

    def parse(
        self,
        document_path: Path,
        mime_type: str,
        *,
        produce_archive: bool = True,
    ):
        lister = CONTENT_LISTERS.get(mime_type)
        if lister is not None:
            try:
                entries = lister.list_entries(document_path)
            except Exception as err:
                # e.g. a corrupt archive, continue as for any other file
                self.log.warning(
                    f"Could not list the contents of {document_path.name}: {err}"
                )
            else:
                # deal with postgresql not allowing NUL bytes in text fields
                self._text = "\n".join(
                    entry.name.replace("\x00", "") for entry in entries
                )
                self._archive_path = self.convert_to_pdf(
                    self.render_listing(document_path.name, mime_type, entries)
                )
                return

        detector = UniversalDetector()
        for line in open(document_path, "rb"):
            detector.feed(line)
            if detector.done:
                break
        detector.close()

        pdf_output = self.html_note.substitute(
            mime_type=mime_type, file_name=str(document_path.name)
        )
        self._text = ""
        encoding = detector.result["encoding"]
        if encoding:
            try:
                with open(document_path, "r", encoding=encoding) as file:
                    self._text = file.read()
                    pdf_output = self._text
                    file.close()
                    # deal with postgresql not allowing NUL bytes in text fields. In this case let self._text be empty
                    # and create PDF with default note
                    if "\x00" not in self._text:
                        pdf_output = f"<HTML><pre>{pdf_output}</pre></HTML>"
                    else:
                        self._text = ""
                        pdf_output = self.html_note.substitute(
                            mime_type=mime_type, file_name=str(document_path.name)
                        )
            except Exception:
                # if there is any exception while reading the file with the guessed encoding use the
                # default values for self._text and pdf_output as already defined
                pass

        self._archive_path = self.convert_to_pdf(pdf_output)

    def render_listing(
        self, file_name: str, mime_type: str, entries: list[ArchiveEntry]
    ) -> str:
        def format_size(size: int | None) -> str:
            if size is None:
                return ""
            for unit in ("B", "KB", "MB", "GB"):
                if size < 1024 or unit == "GB":
                    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
                size /= 1024

        files = [entry for entry in entries if not entry.is_dir]
        total_size = sum(entry.size or 0 for entry in files)
        rendered = entries[: self.max_rendered_entries]

        rows = "\n".join(
            "<tr><td>{}</td><td class='num'>{}</td><td>{}</td></tr>".format(
                escape(entry.name),
                format_size(entry.size),
                entry.modified.strftime("%Y-%m-%d %H:%M") if entry.modified else "",
            )
            for entry in rendered
        )
        omitted = len(entries) - len(rendered)
        omitted_note = (
            f"<p>{omitted} more entries are not shown here, but are included in the document's content.</p>"
            if omitted
            else ""
        )

        return f"""
    <HTML>
    <head><meta charset="utf-8"><style>
    body {{ font-family: sans-serif; font-size: 10pt; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ text-align: left; padding: 2px 6px; border-bottom: 1px solid #ddd; word-break: break-all; }}
    td.num {{ text-align: right; white-space: nowrap; }}
    </style></head>
    <body>
    <b>original file name:</b> {escape(file_name)}<br>
    <b>mime type:</b> {escape(mime_type)}<br>
    <b>entries:</b> {len(files)} files, {len(entries) - len(files)} directories, {format_size(total_size)} uncompressed
    <p>
    <table>
    <tr><th>name</th><th>size</th><th>modified</th></tr>
    {rows}
    </table>
    {omitted_note}
    <p>
    Download original file to work with it.
    </body>
    </HTML>
    """

    def convert_to_pdf(self, content: str):
        pdf_path = Path(self._tempdir) / "convert.pdf"

        with (
            GotenbergClient(
                host=settings.TIKA_GOTENBERG_ENDPOINT,
                timeout=settings.CELERY_TASK_TIME_LIMIT,
            ) as client,
            client.chromium.html_to_pdf() as route,
        ):
            # Set the output format of the resulting PDF
            if settings.OCR_OUTPUT_TYPE in {
                OutputTypeChoices.PDF_A,
                OutputTypeChoices.PDF_A2,
            }:
                route.pdf_format(PdfAFormat.A2b)
            elif settings.OCR_OUTPUT_TYPE == OutputTypeChoices.PDF_A1:
                self.log.warning(
                    "Gotenberg does not support PDF/A-1a, choosing PDF/A-2b instead",
                )
                route.pdf_format(PdfAFormat.A2b)
            elif settings.OCR_OUTPUT_TYPE == OutputTypeChoices.PDF_A3:
                route.pdf_format(PdfAFormat.A3b)

            try:
                index_file_path = Path(self._tempdir) / "index.html"
                with open(index_file_path, "x") as index_file:
                    index_file.write(content)
                    index_file.close()
                response = route.index(index_file_path).run()
                pdf_path.write_bytes(response.content)

                return pdf_path

            except Exception as err:
                raise ParseError(
                    f"Error while converting document to PDF: {err}",
                ) from err

    def get_text(self) -> str:
        return self._text

    def get_date(self) -> "datetime | None":
        return None

    def get_archive_path(self) -> Path | None:
        return self._archive_path

    def get_page_count(self, document_path: Path, mime_type: str) -> int | None:
        return None
