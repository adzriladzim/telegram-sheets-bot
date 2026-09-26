"""Stub verify: every HTML-mode send escapes dynamic values (grep-driven manifest) +
per-handler parser simulation with hostile values (like verify_stats_html.py).

Run: py -3.12 verify_html_escape.py
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HANDLERS = Path(__file__).resolve().parents[2] / "handlers"

TAG = re.compile(r"</?(?:b|i|code|br|u|s|a)(?:\s+[^>]*)?>", re.I)


def fake_parse(text: str) -> None:
    """Emulate Telegram's strict HTML parser (stray '<' -> BadRequest)."""
    rest = TAG.sub("", text)
    if "<" in rest or ">" in rest:
        raise ValueError(
            f"BadRequest: Can't parse entities: stray HTML at offset "
            f"{text.index([c for c in ('<', '>') if c in rest][0])}: {text!r}"
        )


I = "Interp <b><script> & 'Q'"
E = "EVIL <Nama> & Co"


# ---------- Part A: grep-driven manifest per file ----------

A = {
    "rekap.py": [
        "html.escape(c.code)}</b> — {html.escape(c.subject)",
        "ℹ️ {html.escape(c.code)}",
        "html.escape(c.code)} tanggal ini <b>belum lengkap",
        "html.escape(g) for g in st['gaps']",
        "html.escape(nxt)} dari Zoom Record",
        "html.escape(last)}\" if last",
        "html.escape(nxt)}</b> {auto_note",
        "html.escape(k)}:</b> {html.escape(v)}",
    ],
    "schedule.py": [
        "({html.escape(str(c.backup_hari_tanggal))})",
        "html.escape(c.time_range)}",
        "', '.join(html.escape(x) for x in missing)",
        "html.escape(c.room)} | 👤 {html.escape(c.lecturer)} | {html.escape(c.zoom_label)}",
    ],
    "log.py": [
        "html.escape(c.code)}</b> — {html.escape(c.subject)",
        "2️⃣ Pertemuan: <b>{html.escape(nxt)}</b>",
        "html.escape(', '.join(sorted(set(bad))))",
        "• <b>{who}</b> — pertemuan {html.escape(f.get('pertemuan', '') or '')} ({tipe})",
        "html.escape(k)}:</b> {html.escape(v)}",
    ],
    "absen.py": [
        "Kode: <b>{html.escape(kode)}</b>",
        "• Kode: {html.escape(kode)}",
        "html.escape(x) for x in ids[:5]",
    ],
    "backup.py": [
        "html.escape(c.code)}</b> — {html.escape(c.subject)",
        "Pengganti: {html.escape(raw)} → <b>{html.escape(full)}</b>",
        "Pengganti: <b>{html.escape(full)}</b>",
        "html.escape(r.facilitator_awal)",
        "html.escape(r.hari_tanggal)",
        "html.escape(r.jam)",
        "html.escape(r.kode)} — {html.escape(r.subject)",
        "html.escape(r.lecturer)",
        "html.escape(r.room)",
        "html.escape(r.pengganti)",
        "html.escape(r.catatan or '—')",
    ],
    "cancel.py": [
        "html.escape(c.code)}</b> — {html.escape(c.subject)",
        "• Dosen: {html.escape(rec.lecturer)}",
        "• Matkul: {html.escape(rec.subject)}",
        "• Jadwal Awal: {html.escape(rec.jadwal_awal)}",
        "• Jam: {html.escape(rec.jam)}",
        "• Sesi: {html.escape(rec.sesi)}",
        "• Kode: {html.escape(rec.kode)}",
        "• SKS: {html.escape(rec.sks)}",
        "• Fasil: {html.escape(rec.facilitator)}",
    ],
    "start.py": [
        "html.escape(update.effective_user.first_name",
        "html.escape(facilitator)",
        "HELP.format(wib=wib)",  # only dynamic input is int-formatted slots
    ],
    "reminder.py": [
        "html.escape(c.time_range)}</b> {html.escape(c.code)}",
        "html.escape(c.room)} | {html.escape(c.zoom_label)}",
    ],
    "heartbeat.py": [
        "HEARTBEAT_TEXT",  # static, no interpolation
    ],
}

for fname, needles in A.items():
    src = (HANDLERS / fname).read_text(encoding="utf-8")
    for n in needles:
        assert n in src, f"{fname}: missing escaped site {n!r}"
    if fname not in ("heartbeat.py",):
        assert re.search(r"\bimport html\b", src), f"{fname}: missing import html"
print("OK Part A: grep manifest — all %d escape sites present" % sum(len(v) for v in A.values()))


# ---------- Part B: parser simulation per handler ----------

class Capture:
    def __init__(self, chat_id=1):
        self.chat_id = chat_id
        self.html = []
        self.plain = []

    async def reply_text(self, text, *a, **k):
        (self.html if k.get("parse_mode") == "HTML" else self.plain).append(text)
        return SimpleNamespace(delete=self._noop)

    async def delete(self):
        pass

    async def _noop(self):
        return None


class CaptureBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **k):
        self.sent.append((chat_id, text, k))

    async def send_chat_action(self, *a, **k):
        return None


def ce(**over):
    base = dict(code=E, subject=I, day="Senin", time_range="13.00 <jam>", category="Reguler",
                lecturer=I, room=E, rombel="", sks="3", zoom_number="", zoom_link="",
                backup_hari_tanggal="Senin, 8 <September> 2026",
                start_time="13.00 <jam>", zoom_label="Zoom <3>")
    base.update(over)
    return SimpleNamespace(**base)


def async_gen(*vals):
    """Return an async function yielding vals (pretend coroutine for fakes)."""
    async def _fn(*a, **k):
        return vals
    return _fn


def run(coro):
    return asyncio.run(coro)


async def sim_rekap():
    import handlers.rekap as h
    msg = Capture()
    ctx = SimpleNamespace(
        bot_data={"sheets": SimpleNamespace(
            absen_counts=lambda code, n: {"total": 2, "hadir": 2, "tidak": 0, "prodi": ""},
            feedback_counts=lambda *a, **k: {"q": 1},
        )},
        user_data={"cls": ce(), "facilitator": I, "meeting": "3 & 4 <5>",
                   "tipe": "Online", "sesi": I, "peran": E,
                   "bukti": "https://x.test/a?b=1&c=2", "bukti_name": I},
    )
    await h._confirm(msg, ctx)
    for t in msg.html:
        fake_parse(t)
    return msg.html


async def sim_schedule():
    import handlers.schedule as h
    msg = Capture()
    today = "Senin"
    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=1),
                          effective_message=msg, callback_query=None)
    ctx = SimpleNamespace(
        bot=CaptureBot(),
        bot_data={"sheets": SimpleNamespace(
            get_all_loggable_classes=async_gen([ce(day=today, category="Backup")], [], []),
            get_makeup_notes=async_gen({}),
        )},
    )
    with patch("handlers.schedule.users.get", return_value=E), \
         patch("handlers.schedule.sheets.today_day_wib", return_value=today), \
         patch("handlers.schedule.sheets.today_str_wib", return_value="17/09/2026"):
        await h.schedule_cmd(upd, ctx)
    assert msg.html, "schedule: no HTML message captured"
    for t in msg.html:
        fake_parse(t)
    return msg.html


async def sim_log():
    import handlers.log as h
    import sheets
    rec = sheets.LogRecord(facilitator=I, lecture_date="08/09/2026", entry_date="17/09/2026",
                           semester="3 & 4 <a>", subject=I, code=E, meeting="2 <x>",
                           scheme="Online", sks="3", tipe_kelas=I, lecturer=E,
                           start_time="13.00", zoom=E, notes=I)
    t = h._summary(rec)
    fake_parse(t)
    return [t]


async def sim_absen():
    import handlers.absen as h
    msg = Capture()
    q = SimpleNamespace(data="abs:S", message=msg)
    q.answer = lambda: asyncio.sleep(0)
    upd = SimpleNamespace(callback_query=q)
    ctx = SimpleNamespace(user_data={"absen_kode": E, "absen_pertemuan": 3,
                                     "absen_identifiers": [I, "26111600004"]})
    await h.pick_status(upd, ctx)
    assert msg.html, "absen: no HTML message captured"
    for t in msg.html:
        fake_parse(t)
    return msg.html


async def sim_backup():
    import handlers.backup as h
    import sheets
    rec = sheets.BackupRecord(I, "Senin, 8 <Tgl> 2026", "13.00", E, I, E, E, I, I)
    t = h._summary(rec)
    fake_parse(t)
    return [t]


async def sim_cancel():
    import handlers.cancel as h
    msg = Capture()
    ctx = SimpleNamespace(user_data={"cc_cls": ce(), "cc_jadwal": I, "cc_sesi": "3"})
    with patch("handlers.cancel.users.get", return_value=I):
        await h._confirm(msg, ctx)
    assert msg.html, "cancel: no HTML message captured"
    for t in msg.html:
        fake_parse(t)
    return msg.html


async def sim_start():
    import handlers.start as h
    out = []
    # /help (static HELP, dynamic wib slots)
    bot = CaptureBot()
    ctx = SimpleNamespace(bot=bot, bot_data={"cfg": SimpleNamespace(reminder_slots=[(21, 0), (12, 0), (20, 0)])})
    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=1))
    await h.help_cmd(upd, ctx)
    _, t, k = bot.sent[0]
    assert k.get("parse_mode") == "HTML"
    fake_parse(t)
    out.append(t)
    # /start with hostile first name, registered and unregistered
    for first_name, facilitator in ((E, None), (I, E)):
        msg = Capture()
        upd = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(first_name=first_name),
            message=msg,
        )
        ctx = SimpleNamespace(bot=SimpleNamespace(
            send_chat_action=lambda *a, **k: asyncio.sleep(0)))
        with patch("handlers.start.users.get", return_value=facilitator), \
             patch("handlers.start.reminder.register_chat", new=AsyncMock()), \
             patch("handlers.start.heartbeat.register_chat", new=AsyncMock()):
            await h.start_cmd(upd, ctx)
        assert msg.html and not msg.plain, "start: every send must be HTML mode"
        for m in msg.html:
            fake_parse(m)
            assert "<b>TelefasilBot</b>" in m, "start: markup must be present for bold render"
            out.append(m)
    return out


async def sim_reminder():
    import handlers.reminder as h
    bot = CaptureBot()
    send = []
    bot.send_message = lambda cid, text, **k: (send.append((cid, text, k)), asyncio.sleep(0))[1]
    job = SimpleNamespace(chat_id=1, name="reminder:1:2100")
    async def get_all_loggable_classes(f):
        return ([ce(day="Senin")], [], [])
    async def get_done_by_date(f):
        return set()
    ctx = SimpleNamespace(
        job=job, bot=bot,
        bot_data={"cfg": SimpleNamespace(reminder_slots=[(21, 0), (12, 0), (20, 0)]),
                  "sheets": SimpleNamespace(
                      get_all_loggable_classes=get_all_loggable_classes,
                      get_done_by_date=get_done_by_date,
                  )},
    )
    with patch("handlers.reminder.users.get", return_value=E), \
         patch("handlers.reminder.sheets.today_day_wib", return_value="Senin"):
        await h.reminder_job(ctx)
    assert send, "reminder: no message sent"
    for _, t, k in send:
        assert k.get("parse_mode") == "HTML"
        fake_parse(t)
    return [t for _, t, _ in send]


async def sim_heartbeat():
    import handlers.heartbeat as h
    send = []
    ctx = SimpleNamespace(bot=SimpleNamespace(
        send_message=lambda cid, text, **k: (send.append(text), asyncio.sleep(0))[1]))
    with patch("handlers.heartbeat.users.registered_chat_ids", return_value=[1]):
        await h.heartbeat_job(ctx)
    for t in send:
        fake_parse(t)
    return send


SIMS = {
    "rekap": sim_rekap, "schedule": sim_schedule, "log": sim_log,
    "absen": sim_absen, "backup": sim_backup, "cancel": sim_cancel,
    "start": sim_start, "reminder": sim_reminder, "heartbeat": sim_heartbeat,
}

for name, fn in SIMS.items():
    out = run(fn())
    assert out, f"{name}: no HTML sample emitted"
    print(f"OK Part B {name}: {len(out)} msg(s) parseable with hostile values")

print("ALL PASS")