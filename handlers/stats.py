"""/stats [ringkas|detail] [minggu|bulan|semua] — admin only.

Default = RINGAN (1-2 bubble): ringkasan eksekutif (aktif X/Y, sudah-log %,
🔴 top-3 tunggakan + tertua) + Per menu 1 baris + Jarang pakai (nama saja)
+ Belum pernah pakai (nama saja, hitung).
`/stats detail` = laporan lengkap 8 blok: ringkasan eksekutif, per menu,
roster semua fasil, jarang pakai, belum pernah, kelengkapan minggu ini &
tunggakan per fasil, cakupan absen macet, aktivitas 7 hari.
`/stats ringkas` = alias minimal: hanya blok ringkasan eksekutif.
"""
from __future__ import annotations

import html
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

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

_DETAIL_WORDS = {"detail", "rinci", "full", "lengkap"}
_RINGKAS_WORDS = {"ringkas", "ringkasan", "singkat", "short", "summary", "sum"}

_MAX_MSG = 3500  # Telegram caps at 4096 UTF-8 *bytes*; margin for emoji
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


def _b(text: str) -> int:
    return len(text.encode("utf-8"))


def _byte_split(text: str, cap: int = _MAX_MSG) -> list[str]:
    """Cut plain text on UTF-8 byte boundaries (drops a split multibyte tail)."""
    raw = text.encode("utf-8")
    return [raw[i:i + cap].decode("utf-8", "ignore") for i in range(0, len(raw), cap)]


def _chunks(lines: list[str], cap: int = _MAX_MSG) -> list[tuple[str, str | None]]:
    """Pack report lines into (text, parse_mode) messages ≤cap UTF-8 bytes.

    Splits only at line boundaries, so HTML tags never get cut in half (a bare
    ``<b`` fragment is what Telegram rejects). A single line over the cap (rare)
    is stripped of tags and sent as plain text (parse_mode None), byte-split if
    still oversized."""
    out: list[tuple[str, str | None]] = []
    cur: list[str] = []
    n = 0
    for line in lines:
        if _b(line) > cap:
            if cur:
                out.append(("\n".join(cur), ParseMode.HTML))
                cur, n = [], 0
            plain = html.unescape(re.sub(r"<[^>]+>", "", line))
            out += [(p, None) for p in _byte_split(plain, cap)]
            continue
        w = _b(line) + 1
        if cur and n + w > cap:
            out.append(("\n".join(cur), ParseMode.HTML))
            cur, n = [], 0
        cur.append(line)
        n += w
    if cur:
        out.append(("\n".join(cur), ParseMode.HTML))
    return out


def _activity_bars(data: list[dict]) -> list[str]:
    """Last 7 WIB days as text bars (bar capped 40 chars; count always shown)."""
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
        bar = "█" * min(n, 40)
        lines.append(f"{sheets.DAY_ORDER[d.weekday()]} {d.strftime('%d/%m')} {bar} {n}")
    return lines


async def _weekly_sections(sc, names: list[str]) -> tuple[list, list]:
    """(matriks, arrears). matriks: (name, [(kode, log_mark, rekap_mark)]).
    arrears: (name, cls, label, pn, cmpd_ddmmyyyy)."""
    from handlers.log import _parse_backup_date
    matriks: list = []
    arrears: list = []
    today = datetime.now(sheets.WIB).date()
    for name in names:
        try:
            personal, backup, makeup = await sc.get_all_loggable_classes(name)
        except sheets.SheetsError:
            continue
        classes = personal + backup + makeup
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
            if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
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
                         if c.category not in ("Backup", "Make-up") else c.backup_hari_tanggal)
                pn = None
                try:
                    _, nxt, _ = await sc.get_next_meeting(c.code)
                    pn = (await sc.absen_counts(c.code, _first_num(nxt)))["total"] == 0
                except sheets.SheetsError:
                    pn = None  # kode may be absent from absen sheet
                arrears.append((name, c, label, pn, cmpd))
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


def _arr_date(cmpd: str):
    try:
        return datetime.strptime(cmpd, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def _log_pct(matriks: list) -> int:
    tot = logged = 0
    for _, row in matriks:
        for _, lm, _ in row:
            if lm != "?":
                tot += 1
                if lm == "✓":
                    logged += 1
    return round(logged / tot * 100) if tot else 0


def _exec_lines(names: list[str], pdata: list[dict], days: int,
                per_user: dict, matriks: list, arrears: list,
                cov: dict | None) -> list[str]:
    """Blok 1 — ringkasan eksekutif (tanpa bar aktivitas, itu blok 8)."""
    active = sum(1 for n in names if sum(per_user[n].values()) > 0)
    by_name: dict[str, list] = defaultdict(list)
    for name, c, label, pn, cmpd in arrears:
        by_name[name].append((c.code, label, pn, cmpd))

    L = [
        f"👥 Aktif {active}/{len(names)} fasil · ⚡ {len(pdata)} aksi ({_PERIOD_LABEL[days]})",
        f"⚡ Sudah-log {_log_pct(matriks)}% kelas · ⏳ lewat {len(arrears)}",
    ]
    if cov is not None:
        macet = sum(1 for v in cov.values() if not v)
        L.append(f"📋 Absen macet: {macet} kode 0/{_TOTAL_PERTEMUAN}")
    L.append("")
    if by_name:
        L.append("🔴 <b>Perlu perhatian</b>")
        for name in sorted(by_name, key=lambda n: (-len(by_name[n]), n)):
            items = by_name[name]
            dates = [_arr_date(d) for _k, _l, _p, d in items]
            dates = [d for d in dates if d is not None]
            oldest = min(dates).strftime("%d/%m") if dates else "?"
            L.append(f"• {html.escape(name)} — {len(items)} tunggakan (tertua {oldest})")
    else:
        L.append("🟢 Tidak ada tunggakan")
    beres = sorted((n for n in names if n not in by_name),
                   key=lambda n: (-sum(per_user[n].values()), n))
    L.append("")
    L.append(f"✅ <b>Beres semua</b> ({len(beres)} fasil tanpa tunggakan)")
    if beres:
        shown = beres[:50]
        line = "• " + ", ".join(html.escape(n) for n in shown)
        if len(beres) > len(shown):
            line += f" … +{len(beres) - len(shown)} lain"
        L.append(line)
    else:
        L.append("• (tidak ada)")
    return L


def _rare_facil(names: list[str], data: list[dict], per_user: dict,
                days: int) -> tuple[list, int]:
    """(rare, thr) — fasil dengan 0 < total aksi < 3×minggu dalam periode."""
    if days:
        weeks = max(1, round(days / 7))
    else:
        tss: list = []
        for e in data:
            if not isinstance(e, dict):
                continue
            try:
                tss.append(datetime.fromisoformat(str(e.get("ts", ""))))
            except (ValueError, TypeError):
                continue
        span = (datetime.now(sheets.WIB) - min(tss)).days if tss else 0
        weeks = max(1, round(span / 7))
    thr = 3 * weeks
    rare = [n for n in names if 0 < sum(per_user[n].values()) < thr]
    return rare, thr


def _build_summary(names: list[str], data: list[dict], pdata: list[dict], days: int,
                   per_menu: Counter, per_user: dict, last_ts: dict,
                   matriks: list, arrears: list, cov: dict | None) -> list[str]:
    """`/stats ringkas` — hanya blok 1: ringkasan eksekutif."""
    return ["📊 <b>STATS BOT</b> — ringkas"] + _exec_lines(
        names, pdata, days, per_user, matriks, arrears, cov)


def _build_light(names: list[str], data: list[dict], pdata: list[dict], days: int,
                 per_menu: Counter, per_user: dict, last_ts: dict,
                 matriks: list, arrears: list, cov: dict | None) -> list[str]:
    """Default `/stats` — RINGAN: eksekutif (top-3 tunggakan) + Per menu 1 baris
    + Jarang pakai (nama saja) + Belum pernah (nama saja). Roster/matriks/
    tunggakan lengkap/cakupan/aktivitas ada di `/stats detail`."""
    active = sum(1 for n in names if sum(per_user[n].values()) > 0)
    ever = {e.get("name") for e in data if isinstance(e, dict) and isinstance(e.get("name"), str)}
    never = [n for n in names if n not in ever]
    rare, thr = _rare_facil(names, data, per_user, days)

    by_name: dict[str, list] = defaultdict(list)
    for name, c, label, pn, cmpd in arrears:
        by_name[name].append((c.code, label, pn, cmpd))
    top = sorted(by_name, key=lambda n: (-len(by_name[n]), n))[:3]

    L = [
        "📊 <b>STATS BOT</b> — ringan",
        f"👥 Aktif {active}/{len(names)} fasil · ⚡ {len(pdata)} aksi ({_PERIOD_LABEL[days]})",
        f"⚡ Sudah-log {_log_pct(matriks)}% kelas · ⏳ lewat {len(arrears)}",
        "",
    ]
    if top:
        L.append("🔴 <b>Perlu perhatian</b>")
        for name in top:
            items = by_name[name]
            dates = [_arr_date(d) for _k, _l, _p, d in items]
            dates = [d for d in dates if d is not None]
            oldest = min(dates).strftime("%d/%m") if dates else "?"
            L.append(f"• {html.escape(name)} — {len(items)} tunggakan (tertua {oldest})")
        if len(by_name) > 3:
            L.append(f"… +{len(by_name) - 3} fasil lain — lihat /stats detail")
    else:
        L.append("🟢 Tidak ada tunggakan")

    if per_menu:
        parts = " · ".join(f"{html.escape(ACT_LABEL.get(m, m))} {per_menu[m]}x"
                           for m in ACT_ORDER if m in per_menu)
        L.append(f"<b>Per menu</b> — {parts}")
    else:
        L.append("<b>Per menu</b> — (belum ada aksi periode ini)")

    if rare:
        L.append(f"<b>Jarang pakai</b> (kurang dari {thr} aksi/{_PERIOD_LABEL[days]}): "
                 + ", ".join(html.escape(n) for n in sorted(rare)))
    if never:
        L.append(f"<b>Belum pernah pakai ({len(never)})</b>: "
                 + ", ".join(html.escape(n) for n in never))
    return L


def _build_detail(names: list[str], data: list[dict], pdata: list[dict], days: int,
                  per_menu: Counter, per_user: dict, last_ts: dict,
                  matriks: list, arrears: list, cov: dict | None) -> list[str]:
    """Lengkap (default & /detail) — 8 blok: ringkasan, per menu, roster,
    jarang pakai, belum pernah, kelengkapan, tunggakan, cakupan macet, aktivitas."""
    ever = {e.get("name") for e in data if isinstance(e, dict) and isinstance(e.get("name"), str)}
    never = [n for n in names if n not in ever]
    rare, thr = _rare_facil(names, data, per_user, days)

    L = [
        "📊 <b>STATS BOT</b> — lengkap",
        f"👥 {len(names)} terdaftar | ⚡ {len(pdata)} aksi ({_PERIOD_LABEL[days]})",
        "",
        "<b>Ringkasan eksekutif</b>",
    ]
    L += _exec_lines(names, pdata, days, per_user, matriks, arrears, cov)
    L += ["", "<b>Per menu</b>"]
    if per_menu:
        for m in ACT_ORDER + sorted(set(per_menu) - set(ACT_ORDER)):
            if m in per_menu:
                L.append(f"• {html.escape(ACT_LABEL.get(m, m))} — {per_menu[m]}x")
    else:
        L.append("• (belum ada aksi periode ini)")
    L += ["", "<b>Roster semua fasil</b>"]
    roster = sorted(names, key=lambda n: (-sum(per_user[n].values()), n))
    for n in roster:
        c = per_user[n]
        acts = " ".join(f"{ACT_LABEL.get(k, k)} {c.get(k, 0)}" for k in ACT_ORDER)
        ts = html.escape(last_ts.get(n, "")[:16].replace("T", " ")) or "—"
        L.append(f"• {html.escape(n)} — {acts}, terakhir {ts}")
    if rare:
        L += ["", f"<b>Jarang pakai</b> ({_PERIOD_LABEL[days]}, kurang dari {thr} aksi)"]
        L += [f"• {html.escape(n)} ({sum(per_user[n].values())} aksi)" for n in sorted(rare)]
    if never:
        L += ["", f"<b>Belum pernah pakai ({len(never)})</b>",
              "• " + ", ".join(html.escape(n) for n in never)]
    if matriks:
        L += ["", "<b>Kelengkapan minggu ini</b>",
              "<i>● terisi · ○ mendatang · ✗ lewat</i>"]
        for name, row in matriks:
            log_m = [r[1].replace("✓", "●") for r in row]
            rekap_m = [r[2].replace("✓", "●") for r in row]
            ld = sum(1 for m in log_m if m == "●")
            rd = sum(1 for m in rekap_m if m == "●")
            t = len(row)
            L.append(f"<b>▸ {html.escape(name)}</b>")
            L.append(f"<pre>log   {''.join(log_m)} {ld}/{t}\n"
                     f"rekap {''.join(rekap_m)} {rd}/{t}</pre>")
            L.append(", ".join(html.escape(r[0]) for r in row))
    if arrears:
        L += ["", f"<b>Tunggakan per fasil</b> ({len(arrears)})"]
        by_name: dict = defaultdict(list)
        for name, c, label, pn, cmpd in arrears:
            by_name[name].append((c.code, label, pn, cmpd))
        for name in sorted(by_name):
            items = by_name[name]
            kodes = ",".join(html.escape(k) for k, _l, _p, _d in items)
            L.append(f"• {html.escape(name)} — {kodes} ({len(items)})")
            by_date: dict = defaultdict(list)
            for k, lab, _p, _d in items:
                by_date[lab].append(html.escape(k))
            for lab, ks in by_date.items():
                L.append(f"   📅 {html.escape(lab)}: {', '.join(ks)}")
    if cov is not None:
        items = sorted(((k, len(v)) for k, v in cov.items()), key=lambda x: (x[1], x[0]))
        macet = [x for x in items if x[1] == 0]
        partial = sum(1 for x in items if 0 < x[1] < _TOTAL_PERTEMUAN)
        full = sum(1 for x in items if x[1] >= _TOTAL_PERTEMUAN)
        L += ["", "<b>Cakupan absen (macet saja)</b>",
              f"⚠️ macet 0/{_TOTAL_PERTEMUAN}: {len(macet)} · belum penuh: {partial} · lengkap: {full}"]
        for k, _f in macet[:100]:
            L.append(f"• {html.escape(k)}: 0/{_TOTAL_PERTEMUAN} ⚠️")
        if len(macet) > 100:
            L.append(f"… +{len(macet) - 100} kode macet lain")
    L += ["", "<b>Aktivitas 7 hari</b>"]
    L += _activity_bars(data)
    return L


def _parse_args(args: list[str] | None) -> tuple[str, int]:
    """(mode, days); mode: "ringkas" | "ringan" (default) | "lengkap" (detail)."""
    args = args or []
    mode = "ringan"
    period_arg = None
    for a in args:
        k = a.strip().casefold()
        if k in _RINGKAS_WORDS:
            mode = "ringkas"
        elif k in _DETAIL_WORDS:
            mode = "lengkap"
        else:
            period_arg = a
    return mode, _period_days(period_arg)


def _actor(update: Update) -> tuple:
    """(user_id, username, chat_id) — None-safe buat guard admin ganda."""
    u = getattr(update, "effective_user", None)
    c = getattr(update, "effective_chat", None)
    return (getattr(u, "id", None), getattr(u, "username", None), getattr(c, "id", None))


def _admin_ok(uid, cid) -> bool:
    """Admin = effective_user admin ATAU chat admin (private admin chat)."""
    return uid == ADMIN_ID or cid == ADMIN_ID


def _mode_from_cb(cb: str | None) -> str | None:
    """Callback data -> mode ("ringan"|"ringkas"|"lengkap"), None if unknown."""
    if cb == "st:detail":
        return "lengkap"
    if cb == "st:ringkas":
        return "ringkas"
    if isinstance(cb, str) and cb.startswith("st:refresh"):
        parts = cb.split(":")
        mode = parts[2] if len(parts) > 2 else None
        return mode if mode in ("ringan", "ringkas", "lengkap") else None
    return None


def _keyboard(mode: str) -> InlineKeyboardMarkup:
    """Tombol aksi /stats: ganti mode / refresh (ulang mode yang sama)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Detail", callback_data="st:detail"),
        InlineKeyboardButton("📊 Ringkas", callback_data="st:ringkas"),
        InlineKeyboardButton("🔄 Refresh", callback_data=f"st:refresh:{mode}"),
    ]])


async def _generate_report(mode: str, days: int,
                           context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """Hitung laporan penuh (padding command & callback, hasil sama)."""
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

    if mode == "ringkas":
        return _build_summary(names, data, pdata, days, per_menu, per_user, last_ts,
                              matriks, arrears, cov)
    if mode == "ringan":
        return _build_light(names, data, pdata, days, per_menu, per_user, last_ts,
                            matriks, arrears, cov)
    return _build_detail(names, data, pdata, days, per_menu, per_user, last_ts,
                         matriks, arrears, cov)


async def _send_report(reply_text, lines: list[str], mode: str) -> None:
    """Kirim chunk laporan; tombol aksi di bubble TERAKHIR."""
    chunks = _chunks(lines)
    kb = _keyboard(mode)
    for i, (part, pm) in enumerate(chunks, 1):
        log.info("stats chunk %d/%d chars=%d bytes=%d plain=%s",
                 i, len(chunks), len(part), _b(part), pm is None)
        await reply_text(part, parse_mode=pm,
                         reply_markup=kb if i == len(chunks) else None)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, cid = _actor(update)
    if not _admin_ok(uid, cid):
        log.warning("stats denied chat=%s user=%s (@%s)", cid, uid, uname)
        await update.message.reply_text("⛔ Hanya admin bisa lihat stats.")
        return
    mode, days = _parse_args(context.args)
    log.info("stats served to chat=%s user=%s mode=%s", cid, uid, mode)
    from telegram.constants import ChatAction
    try:
        await context.bot.send_chat_action(chat_id=cid, action=ChatAction.TYPING)
    except Exception:
        pass
    busy = await update.message.reply_text("⏳ Hitung statistik...")
    lines = await _generate_report(mode, days, context)

    try:
        await busy.delete()
    except Exception:
        pass
    await _send_report(update.message.reply_text, lines, mode)


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tombol /stats (st:*): mode dari callback data; refresh ulangi mode sama."""
    q = update.callback_query
    await q.answer()
    uid, uname, cid = _actor(update)
    if not _admin_ok(uid, cid):
        log.warning("stats denied chat=%s user=%s (@%s)", cid, uid, uname)
        await q.answer("⛔ Hanya admin bisa lihat stats.", show_alert=True)
        return
    mode = _mode_from_cb(q.data)
    if mode is None:
        log.warning("stats callback unknown data: %r", q.data)
        return
    log.info("stats served to chat=%s user=%s mode=%s", cid, uid, mode)
    from telegram.constants import ChatAction
    try:
        await context.bot.send_chat_action(chat_id=cid, action=ChatAction.TYPING)
    except Exception:
        pass
    busy = await q.message.reply_text("⏳ Hitung statistik...")
    lines = await _generate_report(mode, _period_days(None), context)

    try:
        await busy.delete()
    except Exception:
        pass
    await _send_report(q.message.reply_text, lines, mode)


def register(app: Application, cfg) -> None:
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(stats_callback, pattern=r"^st:"))