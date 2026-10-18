"""
Content listers for container formats like archives.

A content lister reads the table of contents of a file without extracting it.
The default parser stores the listed entry names as the document's content, so
they become searchable, and renders the listing into the generated PDF.

To support another format, write a function which takes the path of the file
and returns a list of ArchiveEntry. Then register it for the format's mime
types in CONTENT_LISTERS together with the default file extension.
"""

import tarfile
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple


class ArchiveEntry(NamedTuple):
    name: str
    size: int | None = None
    modified: datetime | None = None
    is_dir: bool = False


class ContentLister(NamedTuple):
    extension: str
    list_entries: Callable[[Path], list[ArchiveEntry]]


def _zip_timestamp(date_time: tuple[int, int, int, int, int, int]) -> datetime | None:
    try:
        return datetime(*date_time)
    except ValueError:
        # Some archivers write invalid dates like 1980-00-00
        return None


def _tar_timestamp(mtime: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(mtime)
    except (OverflowError, OSError, ValueError):
        return None


def list_zip(path: Path) -> list[ArchiveEntry]:
    """
    Lists the entries of a ZIP archive. Entry names are decoded as UTF-8 if the
    archive flags them as such, otherwise as CP437 as defined by the ZIP spec.
    Entry names are readable even if the archive is password protected.
    """
    with zipfile.ZipFile(path) as archive:
        return [
            ArchiveEntry(
                name=info.filename,
                size=None if info.is_dir() else info.file_size,
                modified=_zip_timestamp(info.date_time),
                is_dir=info.is_dir(),
            )
            for info in archive.infolist()
        ]


def list_tar(path: Path) -> list[ArchiveEntry]:
    """
    Lists the entries of a tar archive, which may be uncompressed or compressed
    with gzip, bzip2 or xz. Raises tarfile.ReadError if the file is a compressed
    single file instead of a tar archive.
    """
    with tarfile.open(path, mode="r:*") as archive:
        return [
            ArchiveEntry(
                name=member.name,
                size=member.size if member.isfile() else None,
                modified=_tar_timestamp(member.mtime),
                is_dir=member.isdir(),
            )
            for member in archive.getmembers()
        ]


CONTENT_LISTERS: dict[str, ContentLister] = {
    "application/zip": ContentLister(".zip", list_zip),
    "application/x-zip-compressed": ContentLister(".zip", list_zip),
    "application/x-tar": ContentLister(".tar", list_tar),
    "application/gzip": ContentLister(".gz", list_tar),
    "application/x-gzip": ContentLister(".gz", list_tar),
    "application/x-bzip2": ContentLister(".bz2", list_tar),
    "application/x-xz": ContentLister(".xz", list_tar),
}
