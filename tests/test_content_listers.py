import gzip
import io
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from paperlessngx_default_parser.content_listers import (
    CONTENT_LISTERS,
    list_tar,
    list_zip,
)


def test_zip_lists_files_and_directories(tmp_path: Path):
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("docs/", "")
        archive.writestr(
            zipfile.ZipInfo("docs/Änderung 2020.rtf", date_time=(2020, 5, 17, 10, 30, 0)),
            "x" * 10,
        )

    entries = list_zip(path)

    assert [entry.name for entry in entries] == ["docs/", "docs/Änderung 2020.rtf"]
    assert entries[0].is_dir and entries[0].size is None
    assert entries[1].size == 10
    assert entries[1].modified == datetime(2020, 5, 17, 10, 30, 0)


def zip_with_raw_names(path: Path, raw_names: list[bytes]) -> Path:
    # zipfile always writes non-ASCII names as UTF-8, so patch the raw names in
    # (without the UTF-8 flag) like Windows programs write them
    # a different letter per name, so no placeholder contains another one
    placeholders = [chr(ord("A") + index).encode() * len(raw) for index, raw in enumerate(raw_names)]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for placeholder in placeholders:
            archive.writestr(placeholder.decode(), "x")
    data = buffer.getvalue()
    for placeholder, raw in zip(placeholders, raw_names):
        data = data.replace(placeholder, raw)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("encoding", "names"),
    [
        # names as written by older DOS/Windows archivers following the ZIP spec
        ("cp437", ["Änderung.txt", "Übersicht Konten.xlsx"]),
        ("cp437", ["Grüße.txt"]),
        # names in the Windows ANSI code page, e.g. from Windows backup tools
        ("cp1252", ["Antrag auf schlichte Änderung - 2020.rtf", "EÜR Gewerbe.pdf"]),
        ("cp1252", ["Größe.txt", "Café.txt"]),
    ],
)
def test_zip_decodes_names_without_utf8_flag(tmp_path: Path, encoding: str, names: list[str]):
    path = zip_with_raw_names(tmp_path / "legacy.zip", [name.encode(encoding) for name in names])

    assert [entry.name for entry in list_zip(path)] == names


def test_zip_lists_names_of_encrypted_archive(tmp_path: Path):
    # zipfile can't write encrypted archives, so set the encryption flag only;
    # listing must not need to decrypt anything
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("secret.txt", "content")
    data = bytearray(buffer.getvalue())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = data.find(signature)
        data[position + flag_offset] |= 0x01
    path = tmp_path / "encrypted.zip"
    path.write_bytes(bytes(data))

    assert [entry.name for entry in list_zip(path)] == ["secret.txt"]


def test_zip_with_invalid_date(tmp_path: Path):
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("a.txt", date_time=(1980, 0, 0, 0, 0, 0)), "a")

    assert list_zip(path)[0].modified is None


@pytest.mark.parametrize("mode", ["w", "w:gz", "w:bz2", "w:xz"])
def test_tar_lists_entries(tmp_path: Path, mode: str):
    path = tmp_path / "test.tar"
    with tarfile.open(path, mode) as archive:
        directory = tarfile.TarInfo("Belege")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        member = tarfile.TarInfo("Belege/Übersicht.csv")
        member.size = 3
        member.mtime = datetime(2024, 1, 2, 3, 4, 5).timestamp()
        archive.addfile(member, io.BytesIO(b"a;b"))

    entries = list_tar(path)

    assert [entry.name for entry in entries] == ["Belege", "Belege/Übersicht.csv"]
    assert entries[0].is_dir and entries[0].size is None
    assert entries[1].size == 3
    assert entries[1].modified == datetime(2024, 1, 2, 3, 4, 5)


def test_gzip_of_single_file_is_not_a_tar(tmp_path: Path):
    path = tmp_path / "file.txt.gz"
    path.write_bytes(gzip.compress(b"just text"))

    with pytest.raises(tarfile.ReadError):
        list_tar(path)


def test_corrupt_zip_raises(tmp_path: Path):
    path = tmp_path / "corrupt.zip"
    path.write_bytes(b"PK\x03\x04 not really a zip")

    with pytest.raises(zipfile.BadZipFile):
        list_zip(path)


def test_registered_extensions():
    assert CONTENT_LISTERS["application/zip"].extension == ".zip"
    assert all(lister.extension.startswith(".") for lister in CONTENT_LISTERS.values())
