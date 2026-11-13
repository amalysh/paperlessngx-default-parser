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
import unicodedata
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


def _unlikely_chars(name: str) -> int:
    # e.g. box drawing characters, which CP437 yields for names in Windows-1252
    return sum(
        1
        for char in name
        if not char.isascii() and not unicodedata.name(char, "").startswith("LATIN")
    )


def _legacy_zip_encoding(raw_names: list[bytes]) -> str:
    """
    Names without the UTF-8 flag should be encoded as CP437, but many Windows
    programs use the ANSI code page instead. Prefer Windows-1252 if it decodes
    all names with fewer unlikely characters.
    """
    try:
        ansi_names = [raw.decode("cp1252") for raw in raw_names]
    except UnicodeDecodeError:
        return "cp437"
    oem_names = [raw.decode("cp437") for raw in raw_names]
    if sum(map(_unlikely_chars, ansi_names)) < sum(map(_unlikely_chars, oem_names)):
        return "cp1252"
    return "cp437"


def list_zip(path: Path) -> list[ArchiveEntry]:
    """
    Lists the entries of a ZIP archive. Entry names are decoded as UTF-8 if the
    archive flags them as such, otherwise as CP437 as defined by the ZIP spec or
    as Windows-1252 if that fits better.
    Entry names are readable even if the archive is password protected.
    """
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()

    # zipfile decodes names without the UTF-8 flag as CP437, which maps every byte
    legacy_names = {
        info.filename: info.filename.encode("cp437")
        for info in infos
        if not info.flag_bits & 0x800 and not info.filename.isascii()
    }
    encoding = _legacy_zip_encoding(list(legacy_names.values()))

    def name(info: zipfile.ZipInfo) -> str:
        if encoding != "cp437" and info.filename in legacy_names:
            return legacy_names[info.filename].decode(encoding)
        return info.filename

    return [
        ArchiveEntry(
            name=name(info),
            size=None if info.is_dir() else info.file_size,
            modified=_zip_timestamp(info.date_time),
            is_dir=info.is_dir(),
        )
        for info in infos
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


def format_size(size: int | None) -> str:
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"


def describe(entries: list[ArchiveEntry]) -> str:
    files = [entry for entry in entries if not entry.is_dir]
    total_size = sum(entry.size or 0 for entry in files)
    return (
        f"{len(files)} files, {len(entries) - len(files)} directories, "
        f"{format_size(total_size)} uncompressed"
    )


CONTENT_LISTERS: dict[str, ContentLister] = {
    "application/zip": ContentLister(".zip", list_zip),
    "application/x-zip-compressed": ContentLister(".zip", list_zip),
    "application/x-tar": ContentLister(".tar", list_tar),
    "application/gzip": ContentLister(".gz", list_tar),
    "application/x-gzip": ContentLister(".gz", list_tar),
    "application/x-bzip2": ContentLister(".bz2", list_tar),
    "application/x-xz": ContentLister(".xz", list_tar),
}
