"""Verification stub: bukti filename format/sanitization + document mime accept/reject.

Run:  py verify_doc_bukti.py
Covers:
  1. bukti_filename follows {tanggal}_{dosen}_{matkul}_{nama_fasil}.{ext}
  2. illegal Drive chars (/ \\ : * ? " < > | _) sanitized out of filename
  3. _doc_ext accepts image/* mime (via mime or filename), rejects non-image
"""
import re
from types import SimpleNamespace

import sheets
from handlers.rekap import _doc_ext

ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
FAIL = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("PASS" if cond else "FAIL"), "-", label, detail)
    if not cond:
        FAIL.append(label)


# --- 1+2: filename format + sanitization ---
rec = sheets.RekapRecord(
    facilitator="Fasil? Nama/2",
    tanggal="7 September 2026",
    lecturer="Dr. Budi:PA/X",
    jam="08:00",
    kode="CS101",
    subject="Matkul \\ Algoritma*",
    sks="3",
    pertemuan="1",
    tipe="Online",
    sesi="Kelas Biasa",
    peran="Fasilitator Kelas",
    bukti="",
)

fn = rec.bukti_filename("jpg")
check("format 4 segmen {tanggal}_{dosen}_{matkul}_{nama_fasil}.jpg",
      bool(re.fullmatch(r"[^_]+_[^_]+_[^_]+_[^_]+\.(jpg|png)", fn)), fn)
check("no illegal Drive chars", not ILLEGAL.search(fn), fn)
check("ext jpg", fn.endswith(".jpg"), fn)
check("png ext respected", rec.bukti_filename("png").endswith(".png"))
check("ext sanitized (strips dot)", rec.bukti_filename(".jpeg").endswith(".jpeg"))

fn2 = rec.bukti_filename("jpg")
print("  contoh:", fn2)

# fallback segments when empty
rec2 = sheets.RekapRecord(
    facilitator="", tanggal="", lecturer="", subject="", jam="", kode="", sks="",
    pertemuan="", tipe="", sesi="", peran="", bukti="",
)
fn3 = rec2.bukti_filename("jpg")
check("fallback segmen tidak kosong", len(fn3.split("_")) == 4, fn3)

# --- 3: document mime accept/reject ---
def doc(mime, name=""):
    return SimpleNamespace(file_name=name, mime_type=mime)

check("accept image/png", _doc_ext(doc("image/png", "a.png")) == "png")
check("accept image/jpeg -> jpg", _doc_ext(doc("image/jpeg", "a.jpg")) == "jpg")
check("accept image/webp", _doc_ext(doc("image/webp", "a.webp")) == "webp")
check("reject application/pdf", _doc_ext(doc("application/pdf", "a.pdf")) is None)
check("reject text/plain", _doc_ext(doc("text/plain", "a.txt")) is None)
check("accept no-mime but .png ext", _doc_ext(doc(None, "scan.png")) == "png")
check("reject no-mime .pdf", _doc_ext(doc(None, "scan.pdf")) is None)
check("reject video mime", _doc_ext(doc("video/mp4", "a.mp4")) is None)

print()
if FAIL:
    print("FAILED:", FAIL)
    raise SystemExit(1)
print("OK: all checks passed")