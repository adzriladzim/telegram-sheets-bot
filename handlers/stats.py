"""/stats [minggu|bulan|semua] — admin only: usage roster, weekly completeness,
arrears, absen coverage, 7-day activity bars."""
from __future__ import annotations

import html
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import sheets
import usage
import users

log = logging.getLogger(__name__)

ADMIN_ID = 2061872254

ACT_LABEL = {"zoom": "log", "absen": "absen", "rekap": "rekap",
             "backup": "backup", "cancel": "cancel"}
ACT_ORDER = ["zoom", "absen", "rekap", "backup", "cancel"]

PERIODS = {
    "minggu": 7, "mingguan": 7, "week": 7, "weekly": 7, "7": 7,
    "bulan": 30, "bulanan": 30, "month": 30, "monthly": 30, "30": 30,
    "semua": 0, "all": 0, "total": 0,
}
_PERIOD_LABEL = {7: "7 hari terakhir", 30: "30 hari terakhir", 0: "sepanjang waktu"}

_MAX_MSG = 4000  # telegram soft cap; keep under 4096
_TOTAL_PERTEMUAN = 16


def _first_num(s: str) -> int:
    m = re.search(r"\d+", s or "")
    return int(m.group(0)) if m else 1


def _period_days(arg: str | None) -> int:
    if not arg:
        return 7
    key = re.sub(r"[^a-z0-9]", "", arg.strip().casefold())
    return PERIODS.get(key, 7)


def _in_period(ts: str, days: int) -> bool:
    if not days:
        return True
    try:
        return datetime.fromisoformat(ts) >= datetime.now(sheets.WIB) - timedelta(days=days)
    except (ValueError, TypeError):
        return True


_HARD_SPLIT = 3800  # single oversized line: force-cut, keep under Telegram 4096 cap


def _chunks(lines: list[str], limit: int = _MAX_MSG) -> list[str]:
    """Split lines into ≤limit-char messages; tries to keep lines whole (HTML tags
    intact), but force-splits any single line longer than the limit using a safe
    break near a space so a growing report can't overflow Telegram's 4096 cap."""
    out: list[str] = []
    cur: list[str] = []
    n = 0
    for line in lines:
        while len(line) + 1 > limit:
            hard = min(_HARD_SPLIT, len(line))
            cut = line.rfind(" ", 0, hard)
            if cut <= 0:
                cut = hard
            if cur:
                out.append("\n".join(cur))
                cur, n = [], 0
            out.append(line[:cut])
            line = line[cut:].lstrip()
        w = len(line) + 1
        if line:
            if cur and n + w > limit:
                out.append("\n".join(cur))
                cur, n = [], 0
            cur.append(line)
            n += w
    if cur:
        out.append("\n".join(cur))
    return out


def _activity_bars(data: list[dict]) -> list[str]:
    """Last 7 WIB days as text bars (cap 40 chars)."""
    by_day: Counter = Counter()
    for e in data:
        if not isinstance(e, dict):
            continue
        try:
            d = datetime.fromisoformat(str(e.get("ts", "")))
        except (ValueError, TypeError):
            continue
        by_day[d.date()] += 1
    lines = []
    today = datetime.now(sheets.WIB).date()
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        n = by_day.get(d, 0)
        lines.append(f"{sheets.DAY_ORDER[d.weekday()]} {d.strftime('%d/%m')} {'█' * min(n, 40)} {n}")
    return lines


async def _weekly_sections(sc, names: list[str]) -> tuple[list, list]:
    """(matriks rows, arrears rows). matriks: (name, [(kode, log_mark, rekap_mark)])."""
    from handlers.log import _parse_backup_date
    matriks: list = []
    arrears: list = []
    today = datetime.now(sheets.WIB).date()
    for name in names:
        try:
            personal, backup = await sc.get_all_loggable_classes(name)
        except sheets.SheetsError:
            continue
        classes = personal + backup
        if not classes:
            continue
        try:
            done = await sc.get_done_by_date(name)
        except sheets.SheetsError:
            done = set()
        try:
            complete, _ = await sc.get_rekap_status(name)
        except sheets.SheetsError:
            complete = set()
        row = []
        for c in classes:
            if c.category == "Backup" and c.backup_hari_tanggal:
                cmpd = _parse_backup_date(c.backup_hari_tanggal)
            else:
                cmpd = sheets.next_date_for_day(c.day)
            key = (c.code.casefold(), cmpd)
            log_done = key in done
            rekap_done = any(key in complete or (c.code.casefold(), s) in complete
                             for s in (cmpd, sheets.tanggal_panjang(cmpd)))
            lm = "✓" if log_done else ("?" if c.day == "?" else "○")
            rm = "✓" if rekap_done else ("?" if c.day == "?" else "○")
            if not log_done:
                try:
                    cdate = datetime.strptime(cmpd, "%d/%m/%Y").date()
                    lm = "✗" if cdate <= today else "○"
                except ValueError:
                    pass
            if not rekap_done and lm == "✓":
                try:
                    cdate = datetime.strptime(cmpd, "%d/%m/%Y").date()
                    rm = "✗" if cdate <= today else "○"
                except ValueError:
                    pass
            row.append((c.code, lm, rm))
            if lm == "✗":
                label = (f"{c.day} {sheets.tanggal_panjang(cmpd)}"
                         if c.category != "Backup" else c.backup_hari_tanggal)
                pn = None
                try:
                    _, nxt, _ = await sc.get_next_meeting(c.code)
                    pn = (await sc.absen_counts(c.code, _first_num(nxt)))["total"] == 0
                except sheets.SheetsError:
                    pn = None  # kode may be absent from absen sheet
                arrears.append((name, c, label, pn))
        if row:
            matriks.append((name, row))
    return matriks, arrears


def _filter_period(data: list[dict], days: int) -> list[dict]:
    if not days:
        return [e for e in data if isinstance(e, dict)]
    out: list[dict] = []
    for e in data:
        if not isinstance(e, dict):
            log.warning("skip corrupt usage entry: %r", e)
            continue
        if _in_period(str(e.get("ts", "")), days):
            out.append(e)
    return out


def _build_report(names: list[str], data: list[dict], pdata: list[dict], days: int,
                  per_menu: Counter, per_user: dict, last_ts: dict,
                  matriks: list, arrears: list, cov: dict | None) -> list[str]:
    """Render the full report as HTML lines (chunked later by _chunks)."""
    ever = {e.get("name") for e in data if isinstance(e, dict) and isinstance(e.get("name"), str)}
    if days:
        weeks = max(1, round(days / 7))
    else:
        tss = []
        for e in data:
            if not isinstance(e, dict):
                continue
            try:
                tss.append(datetime.fromisoformat(str(e.get("ts"))))
            except (ValueError, TypeError, KeyError):
                continue
        span = (datetime.now(sheets.WIB) - min(tss)).days if tss else 0
        weeks = max(1, round(span / 7))
    thr = 3 * weeks
    rare = [n for n in names if 0 < sum(per_user[n].values()) < thr]
    never = [n for n in names if n not in ever]

    L: list[str] = [
        "📊 <b>STATS BOT</b>",
        f"👥 {len(names)} terdaftar | ⚡ {len(pdata)} aksi ({_PERIOD_LABEL[days]})",
        "",
        "<b>Per menu</b>",
    ]
    if per_menu:
        for m in ACT_ORDER + sorted(set(per_menu) - set(ACT_ORDER)):
            if m in per_menu:
                L.append(f"• {ACT_LABEL.get(m, m)} — {per_menu[m]}x")
    else:
        L.append("• (belum ada aksi periode ini)")
    L += ["", "<b>Roster semua fasil</b>"]
    roster = sorted(names, key=lambda n: (-sum(per_user[n].values()), n))
    for n in roster:
        c = per_user[n]
        acts = " ".join(f"{ACT_LABEL.get(k, k)} {c.get(k, 0)}" for k in ACT_ORDER)
        ts = last_ts.get(n, "")[:16].replace("T", " ") or "—"
        L.append(f"• {html.escape(n)} — {acts}, terakhir {ts}")
    if rare:
        L += ["", f"<b>Jarang pakai</b> ({_PERIOD_LABEL[days]}, <{thr} aksi)"]
        L += [f"• {html.escape(n)} ({sum(per_user[n].values())} aksi)" for n in sorted(rare)]
    if never:
        L += ["", f"<b>Belum pernah pakai ({len(never)})</b>"]
        L += [f"• {html.escape(n)}" for n in never]
    if matriks:
        L += ["", "<b>Kelengkapan minggu ini</b>",
              "<i>✓ terisi · ✗ lewat belum · ○ jadwal mendatang</i>"]
        for name, row in matriks:
            codes = ",".join(r[0] for r in row)
            L.append(f"• {html.escape(name)} — {codes}")
            L.append(f"   log {' '.join(r[1] for r in row)} | rekap {' '.join(r[2] for r in row)}")
    if arrears:
        L += ["", f"<b>Tunggakan</b> (kelas lewat, belum di-log: {len(arrears)})"]
        by_name: dict = defaultdict(list)
        for name, c, label, pn in arrears:
            by_name[name].append((c, label, pn))
        for name in sorted(by_name):
            items = []
            for c, label, pn in by_name[name]:
                extra = " [absen belum]" if pn else (" [absen ada]" if pn is not None else "")
                items.append(f"{c.code} ({label}){extra}")
            L.append(f"• {html.escape(name)}: {', '.join(items)}")
    if cov is not None:
        items = sorted(((k, len(v)) for k, v in cov.items()), key=lambda x: (x[1], x[0]))
        L += ["", f"<b>Cakupan absen per kode</b> — {len(items)} kode, pertemuan terisi/{_TOTAL_PERTEMUAN}"]
        if items:
            for k, f in items[:10]:
                warn = " ⚠️" if f == 0 else ""
                L.append(f"• {html.escape(k)}: {f}/{_TOTAL_PERTEMUAN}{warn}")
        else:
            L.append("• (belum ada)")
    L += ["", "<b>Aktivitas 7 hari</b>"]
    L += _activity_bars(data)
    return L


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("⛔ Hanya admin bisa lihat stats.")
        return
    from telegram.constants import ChatAction
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except Exception:
        pass
    busy = await update.message.reply_text("⏳ Hitung statistik...")
    days = _period_days((context.args or [None])[0])
    all_users = users.registry().all()
    names = sorted(all_users.values())

    data, _, _ = usage.stats()
    pdata = _filter_period(data, days)

    per_menu = Counter(e.get("action", "?") for e in pdata if isinstance(e, dict))
    per_user: dict[str, Counter] = defaultdict(Counter)
    last_ts: dict[str, str] = {}
    for e in pdata:
        if not isinstance(e, dict):
            log.warning("skip corrupt usage entry: %r", e)
            continue
        name, action = e.get("name"), e.get("action")
        if not isinstance(name, str) or not isinstance(action, str):
            log.warning("skip corrupt usage entry: %r", e)
            continue
        per_user[name][action] += 1
        last_ts[name] = str(e.get("ts", ""))

    sc = context.bot_data["sheets"]
    matriks: list = []
    arrears: list = []
    try:
        matriks, arrears = await _weekly_sections(sc, names)
    except Exception:
        pass
    cov: dict | None = None
    try:
        cov = await sc.absen_coverage()
    except sheets.SheetsError:
        cov = None

    L = _build_report(names, data, pdata, days, per_menu, per_user, last_ts,
                      matriks, arrears, cov)

    try:
        await busy.delete()
    except Exception:
        pass
    chunks = _chunks(L)
    for i, part in enumerate(chunks, 1):
        log.info("stats chunk %d/%d len=%d", i, len(chunks), len(part))
        await update.message.reply_text(part, parse_mode=ParseMode.HTML)


def register(app: Application, cfg) -> None:
    app.add_handler(CommandHandler("stats", stats_cmd))