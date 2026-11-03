"""read_document: 자료 폴더의 파일만 읽는다. PDF·HWP·HWPX·docx·텍스트·그림."""

import json
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

import pymupdf
import pytest
from mcp import Client

from openstem.server import mcp
from openstem.tools.documents import read_document
from openstem.tools.export import pandoc_path
from openstem.workers.documents import hwp_records, para_text

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    folder = tmp_path / "자료"
    folder.mkdir()
    monkeypatch.setenv("OPENSTEM_INPUT_DIR", str(folder))
    return folder


def make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for text in ("Momentum p = mv", "Impulse J = F dt"):
        doc.new_page().insert_text((72, 72), text)
    scanned = doc.new_page()  # 글자 없이 그림만 있는 쪽(스캔본 흉내)
    scanned.insert_image(pymupdf.Rect(72, 72, 300, 200), filename=str(EXAMPLES / "rc_circuit.png"))
    doc.new_page().insert_text((72, 72), "Ch. 3")  # 짧은 표지 쪽은 스캔본으로 보지 않는다
    doc.save(path)


def make_hwpx(path: Path) -> None:
    section = (
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        "<hp:p><hp:run><hp:t>과제 공지</hp:t></hp:run></hp:p>"
        "<hp:p><hp:run><hp:t>마감은 </hp:t></hp:run><hp:run><hp:t>11월 10일</hp:t></hp:run></hp:p>"
        "</hs:sec>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", section)


def test_listing_shows_files_and_folder(inbox):
    (inbox / "물리").mkdir()
    (inbox / "물리" / "노트.txt").write_text("운동량", encoding="utf-8")
    (inbox / "무시.exe").write_bytes(b"MZ")
    result = read_document()
    assert result.ok and result.data["folder"] == str(inbox)
    assert [f["path"] for f in result.data["files"]] == ["물리/노트.txt"]


def test_empty_folder_tells_where_to_put_files(inbox):
    assert str(inbox) in read_document().data["note"]


def test_pdf_text_pages_and_scanned_hint(inbox):
    make_pdf(inbox / "강의.pdf")
    result = read_document("강의.pdf")
    assert result.ok and result.data["pages"] == 4
    assert "[page 1]\nMomentum" in result.data["text"] and "Impulse" in result.data["text"]
    assert result.data["scanned_pages"] == [3]
    only_two = read_document("강의.pdf", pages="2")
    assert "Momentum" not in only_two.data["text"] and "Impulse" in only_two.data["text"]


def test_pdf_page_images(inbox):
    make_pdf(inbox / "강의.pdf")
    result = read_document("강의.pdf", pages="1-2", mode="images")
    assert result.ok and result.data["image_pages"] == [1, 2]
    assert result.preview_png.startswith(b"\x89PNG") and len(result.more_images) == 1


def test_hwpx_paragraphs(inbox):
    make_hwpx(inbox / "공지.hwpx")
    result = read_document("공지.hwpx")
    assert result.ok and result.data["text"] == "과제 공지\n마감은 11월 10일"


def test_docx_with_math_through_pandoc(inbox, tmp_path):
    md = tmp_path / "src.md"
    md.write_text("# 운동량\n\n$p = mv$ 이다.\n", encoding="utf-8")
    subprocess.run([pandoc_path(), str(md), "-o", str(inbox / "보고서.docx")], check=True)
    result = read_document("보고서.docx")
    assert result.ok and "운동량" in result.data["text"] and "p = mv" in result.data["text"]


def test_cp949_text_file(inbox):
    (inbox / "옛날.txt").write_bytes("한글 메모".encode("cp949"))
    assert read_document("옛날.txt").data["text"] == "한글 메모"


def test_image_file_comes_back_as_image(inbox):
    shutil.copyfile(EXAMPLES / "rc_circuit.png", inbox / "문제.png")
    result = read_document("문제.png")
    assert result.ok and result.preview_png.startswith(b"\x89PNG")


def test_long_text_is_split_into_parts(inbox):
    (inbox / "긴글.txt").write_text("가" * 70_000, encoding="utf-8")
    first = read_document("긴글.txt")
    assert first.data["parts"] == 3 and len(first.data["text"]) == 30_000 and "part=2" in first.data["next"]
    assert len(read_document("긴글.txt", part=3).data["text"]) == 10_000
    assert not read_document("긴글.txt", part=4).ok


@pytest.mark.parametrize(
    ("make_path", "fragment"),
    [
        (lambda inbox, tmp: "../밖.txt", "outside the materials folder"),
        (lambda inbox, tmp: str(tmp / "밖.txt"), "outside the materials folder"),
        (lambda inbox, tmp: "실행.exe", "Unsupported format"),
        (lambda inbox, tmp: "없는파일.pdf", "not found"),
    ],
)
def test_files_outside_or_unsupported_are_refused(inbox, tmp_path, make_path, fragment):
    (tmp_path / "밖.txt").write_text("비밀", encoding="utf-8")
    result = read_document(make_path(inbox, tmp_path))
    assert not result.ok and fragment in " ".join(result.data["errors"])


def test_bad_pages_and_images_mode_on_docx(inbox):
    (inbox / "a.txt").write_text("x", encoding="utf-8")
    assert "Invalid pages" in read_document("a.txt", pages="1-").data["errors"][0]
    assert not read_document("a.txt", pages="3-1").ok
    assert "only for PDF and image" in read_document("a.txt", mode="images").data["errors"][0]


def utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


def test_hwp_para_text_skips_controls():
    # 8글자 크기 확장 제어(11, 표·그림 자리)와 한 글자 제어(13, 문단 끝)는 빼고, 탭(9)은 남긴다
    extended = struct.pack("<8H", 11, 0, 0, 0, 0, 0, 0, 11)
    tab = struct.pack("<8H", 9, 0, 0, 0, 0, 0, 0, 9)
    raw = utf16("힘") + extended + utf16("F") + tab + utf16("= ma") + struct.pack("<H", 13)
    assert para_text(raw) == "힘F\t= ma"


def test_hwp_records_reads_para_text_records():
    def record(tag: int, body: bytes) -> bytes:
        return struct.pack("<I", tag | (len(body) << 20)) + body

    data = record(66, b"\x00" * 8) + record(67, utf16("첫 문단")) + record(67, utf16("둘째") + struct.pack("<H", 13))
    assert hwp_records(data) == ["첫 문단", "둘째"]


@pytest.mark.anyio
async def test_mcp_returns_page_images(inbox):
    make_pdf(inbox / "강의.pdf")
    async with Client(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert "read_document" in names
        result = await client.call_tool("read_document", {"path": "강의.pdf", "pages": "1-2", "mode": "images"})
        assert not result.is_error, result.content[0].text
        assert json.loads(result.content[0].text)["image_pages"] == [1, 2]
        assert [b.type for b in result.content] == ["text", "image", "image"]
