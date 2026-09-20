"""/zoom pola config — data/pola.json (opsional). Tanpa config = fitur mati, jalan normal.

Schema (sederhana):
{
  "PPC01": {"pertemuan": {"1": "A", "2": "B", "3": "A", "4": "B"}}
}

Nilai = fragmen fasil (mis "Budi" / "Fasil B") — dibandingkan ke nama fasilitator
terdaftar (contains, casefold). Label non-nama ("A"/"B") juga valid: tak cocok ke
sembarang nama -> warning 🟡 informatif tiap pertemuan yang dipetakan.

Lokasi: env POLA_JSON_PATH (path file) -> fallback data/pola.json. data/ di-gitignore
(seperti users.json) — isi/drop manual saat deploy (Railway volume /app/data).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "pola.json"


def _load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else Path(os.getenv("POLA_JSON_PATH", "").strip() or DEFAULT_PATH)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, str]] = {}
    for kode, body in (raw or {}).items():
        if not isinstance(body, dict):
            continue
        pmap = body.get("pertemuan")
        if not isinstance(pmap, dict):
            continue
        out[kode.strip().casefold()] = {
            str(k).strip(): str(v).strip() for k, v in pmap.items() if str(v).strip()
        }
    return out


def pola_for(kode: str) -> dict[str, str] | None:
    """Peta {nomor_pertemuan: fasil-fragment} utk kode, atau None (tak ada config)."""
    return _load().get((kode or "").strip().casefold())