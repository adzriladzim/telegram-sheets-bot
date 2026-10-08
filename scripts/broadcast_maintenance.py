"""Broadcast maintenance TelefasilBot ke semua fasil terdaftar.

Pakai Bot API sendMessage langsung (tanpa polling) -> AMAN jalan bareng
instance Railway yang lagi polling (konflik getUpdates cuma kalau 2 poller).

Pakai:
  py scripts/broadcast_maintenance.py --mode awal              (dry-run, default)
  py scripts/broadcast_maintenance.py --mode awal --send       (kirim beneran)
  py scripts/broadcast_maintenance.py --mode ingat --send      (pengingat)
  py scripts/broadcast_maintenance.py --mode awal --send --only 2061872254  (tes ke 1 chat dulu)

Mode:
  awal  = info awal (dikirim hari ini)
  ingat = pengingat (dikirim besok/Sabtu, prefix 1 baris + body sama)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_token() -> str:
    env_file = BASE_DIR / ".env"
    tok = ""
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN"):
                _, _, v = line.partition("=")
                tok = v.strip().strip('"').strip("'")
                break
    if not tok:
        import os

        tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tok:
        sys.exit("TELEGRAM_BOT_TOKEN kosong (cek .env).")
    return tok


BODY = (
    "\U0001f527 <b>Info Pemeliharaan \u2014 TelefasilBot nonaktif sementara</b>\n"
    "\n"
    "Halo kak! <b>Sabtu, 10 Okt jam 11:00 WIB</b> bot nonaktif sementara "
    "untuk pemeliharaan sistem. \U0001f64f\n"
    "\n"
    "\u2022 /zoom /absen /rekap /backup /cancel /schedule belum bisa dipakai dulu\n"
    "\u2022 Catat manual dulu aja (foto absen / notes), nanti input lagi setelah bot nyala\n"
    "\u2022 Info nyala lagi dikabarin menyusul\n"
    "\n"
    "Makasih pengertiannya ya!"
)

PREFIX_INGAT = "\U0001f4cc Pengingat buat Sabtu ini ya kak \u2014 pesannya sama kayak kemarin \U0001f447\n\n"

MSGS = {"awal": BODY, "ingat": PREFIX_INGAT + BODY}


def _load_users() -> dict[str, str]:
    p = BASE_DIR / "data" / "users.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
            return bool(body.get("ok")), "ok" if body.get("ok") else str(body)[:200]
    except Exception as exc:  # noqa: BLE001 - lapor aja
        return False, f"{type(exc).__name__}: {exc}"[:200]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("awal", "ingat"), default="awal")
    ap.add_argument("--send", action="store_true", help="kirim beneran (tanpa ini = dry-run)")
    ap.add_argument("--only", default="", help="kirim cuma ke 1 chat_id (buat tes)")
    args = ap.parse_args()

    token = _load_token()
    users = _load_users()
    if args.only:
        users = {args.only: users.get(args.only, "(tes)")}
    text = MSGS[args.mode]

    print(f"mode={args.mode} target={len(users)} dry_run={not args.send}")
    print("--- preview ---")
    print(text)
    print("--- penerima ---")
    for cid, name in list(users.items())[:5]:
        print(f"  {cid} -> {name}")
    if len(users) > 5:
        print(f"  ... +{len(users) - 5} lagi")
    if not args.send:
        print("DRY-RUN: ga ada pesan terkirim. Tambah --send buat eksekusi.")
        return

    ok, fail = 0, 0
    for i, (cid, name) in enumerate(users.items()):
        good, info = _send(token, cid, text)
        if not good:
            time.sleep(2)
            good, info = _send(token, cid, text)
        print(f"[{'OK' if good else 'FAIL'}] {cid} ({name}): {info}")
        ok, fail = ok + good, fail + (not good)
        if i < len(users) - 1:
            time.sleep(1)
    print(f"SELESAI ok={ok} fail={fail} total={len(users)}")


if __name__ == "__main__":
    main()
