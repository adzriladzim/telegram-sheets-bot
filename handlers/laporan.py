"""/laporan — Laporan Kinerja Dosen PDF (BETA tersembunyi).

Alpha direkayasa, silent-gated via env LAPORAN_BETA_IDS + LAPORAN_BETA_NAMES.
Alur: /laporan -> pilih kelas (personal+backup+makeup, makeup col J!=true sudah
difilter get_makeup_classes) -> pilih pertemuan/range -> join zoom_entries +
tab rekap (group by kode col E) + absen_counts + feedback_counts -> PDF tiru
template contoh (header #4558D0, 4 card skor, line chart teal #1A3C40, tabel
kualitatif, footer) -> sendDocument.

Beta gate: chat_id HARUS ada di LAPORAN_BETA_IDS DAN nama (users.get) di
LAPORAN_BETA_NAMES. Gagal = diam (seperti command tak dikenal), TANPA sebut
/laporan. /laporan tidak muncul di help/schedule/BotCommand.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import re
import threading
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import sheets
import usage
import users
from config import Config, BASE_DIR

log = logging.getLogger(__name__)

CLASS, MEETING = range(2)
_TIMEOUT = 60 * 60
_SKIP_FILTER = filters.TEXT & ~filters.COMMAND

_FONT_DIR = BASE_DIR / "fonts"
_HEADER_BLUE = (69, 88, 208)    # #4558D0
_CHART_TEAL = "#1A3C40"

# plt.subplots/close tidak thread-safe; _build_pdf jalan di thread executor
# dan concurrent_updates=True -> serialisasi chart (per-chat lock tak cukup).
_MATPLOTLIB_LOCK = threading.Lock()


def _sheets(context: ContextTypes.DEFAULT_TYPE) -> sheets.SheetsClient:
    return context.bot_data["sheets"]


# ---------- beta gate ----------

def _beta_ids() -> set[int]:
    raw = os.getenv("LAPORAN_BETA_IDS", "").strip()
    return {int(p) for p in raw.split(",") if p.strip().isdigit()}


def _beta_names() -> set[str]:
    """Separator '|' — nama bisa mengandung koma (mis. 'S.T., M.T.')."""
    return {n.strip().casefold() for n in os.getenv("LAPORAN_BETA_NAMES", "").split("|") if n.strip()}


def _beta_allowed(chat_id: int, name: str | None) -> bool:
    if not name or not _beta_ids() or not _beta_names():
        return False
    return chat_id in _beta_ids() and name.strip().casefold() in _beta_names()


# ---------- helpers ----------

def _first_nums(text: str) -> list[int]:
    """'4' -> [4]; '3-5' -> [3,4,5]; '3 dan 4'/'3,4' -> [3,4]. In range 1-16."""
    ns = sorted({int(n) for n in re.findall(r"\d+", text or "") if 1 <= int(n) <= 16})
    if "-" in re.sub(r"\s", "", text or "") and len(ns) >= 2:
        return list(range(ns[0], ns[-1] + 1))
    return ns


def _meeting_range_label(ps: list[int]) -> str:
    if not ps:
        return "Semua"
    ps = sorted(ps)
    if len(ps) == 1:
        return str(ps[0])
    if ps == list(range(ps[0], ps[-1] + 1)):
        return f"{ps[0]}-{ps[-1]}"
    return ", ".join(map(str, ps))


def _predicate(score: float) -> str:
    if score >= 4.5:
        return "Sangat Baik"
    if score >= 4.0:
        return "Baik"
    if score >= 3.0:
        return "Cukup"
    return "Perlu Perbaikan"


def _pdf_filename(nama: str, kode: str) -> str:
    n = re.sub(r"[^A-Z0-9]+", "-", (nama or "").upper()).strip("-") or "FASIL"
    k = re.sub(r"[^A-Za-z0-9]+", "-", (kode or "").strip()).strip("-") or "KELAS"
    today = datetime.now(usage.WIB)
    return f"Laporan-CSAT-{n}-{k}-{today:%Y%m%d}.pdf"


# ---------- conversation step 1: pick class ----------

async def cmd_laporan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    try:
        if update.callback_query:
            await update.callback_query.answer()
    except Exception:
        pass
    name = users.get(chat_id)
    if not name:
        return ConversationHandler.END
    if not _beta_allowed(chat_id, name):
        # Silent: /laporan tak pernah disebut. Login minimal, tanpa reply.
        log.info("laporan gate denied for chat %s", chat_id)
        return ConversationHandler.END
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    context.user_data["facilitator"] = name
    loading = await update.effective_message.reply_text("⏳ Ambil daftar kelas...")
    try:
        personal, backup, makeup = await _sheets(context).get_all_loggable_classes(name)
    except sheets.SheetsError as exc:
        try:
            await loading.delete()
        except Exception:
            pass
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    classes = personal + backup + makeup
    try:
        await loading.delete()
    except Exception:
        pass
    if not classes:
        await update.effective_message.reply_text("Tidak ada kelas untukmu.")
        return ConversationHandler.END
    kb = []
    for i, c in enumerate(classes):
        suffix = f" ({c.backup_hari_tanggal})" if (c.category in ("Backup", "Make-up") and c.backup_hari_tanggal) else f" ({c.day} {c.time_range})"
        icon = "🔄" if c.category == "Backup" else ("🧪" if c.category == "Make-up" else "⭐")
        label = f"{icon} {c.code} — {html.escape(c.subject)}{suffix}"
        kb.append([InlineKeyboardButton(label, callback_data=f"lpc:{i}")])
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="lpb:cancel")])
    context.user_data["laporan_classes"] = classes
    await update.effective_message.reply_text(
        "1️⃣ Pilih kelas untuk laporan:", reply_markup=InlineKeyboardMarkup(kb))
    return CLASS


# ---------- conversation step 2: pick pertemuan/range ----------

async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    classes: list[sheets.ClassEntry] = context.user_data.get("laporan_classes", [])
    if not (0 <= idx < len(classes)):
        await q.message.reply_text("Kelas tidak valid. Ketik /laporan lagi.")
        return ConversationHandler.END
    c = classes[idx]
    context.user_data["laporan_class"] = c
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except Exception:
        pass
    loading = await q.message.reply_text("⏳ Ambil Zoom Record...")
    try:
        entries = await _sheets(context).zoom_entries(context.user_data["facilitator"])
    except sheets.SheetsError as exc:
        try:
            await loading.delete()
        except Exception:
            pass
        await q.message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    try:
        await loading.delete()
    except Exception:
        pass
    kc = c.code.casefold()
    mine = [e for e in entries if e["kode"].casefold() == kc]
    ps: dict[int, dict] = {}
    for e in mine:
        for n in {int(x) for x in re.findall(r"\d+", e.get("pertemuan", "")) if 1 <= int(x) <= 16}:
            ps.setdefault(n, e)
    ps = dict(sorted(ps.items()))
    if not ps:
        # Fallback 8 pertemuan default agar beta tetap jalan walau tak ada Zoom Record
        ps = {n: {} for n in range(1, 9)}
    context.user_data["laporan_entries"] = mine
    context.user_data["laporan_ps"] = ps
    kb = []
    for n, e in ps.items():
        tgl = e.get("tanggal", "") if isinstance(e, dict) else ""
        suffix = f" · {tgl}" if tgl else ""
        kb.append([InlineKeyboardButton(f"Pertemuan {n}{suffix}", callback_data=f"lpq:{n}")])
    kb.append([InlineKeyboardButton(f"📊 Semua ({len(ps)} pertemuan)", callback_data="lpq:all")])
    kb.append([InlineKeyboardButton("◀️ Kembali", callback_data="lpb:class"),
               InlineKeyboardButton("❌ Batal", callback_data="lpb:cancel")])
    await q.message.reply_text(
        "2️⃣ Pilih pertemuan (atau ketik rentang, mis. 3-5):",
        reply_markup=InlineKeyboardMarkup(kb))
    return MEETING


async def pick_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    raw = q.data.split(":", 1)[1]
    ps_dict: dict[int, dict] = context.user_data.get("laporan_ps", {})
    if raw == "all":
        ps = list(ps_dict.keys())
    else:
        try:
            n = int(raw)
        except ValueError:
            await q.message.reply_text("Data tidak valid. Ketik /laporan lagi.")
            return ConversationHandler.END
        ps = [n]
    return await _generate_and_send(update, context, ps)


async def enter_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    ps = _first_nums(text)
    if not ps:
        await update.effective_message.reply_text("Format tidak dikenali. Contoh: 4 atau 3-5.")
        return MEETING
    return await _generate_and_send(update, context, ps)


# ---------- generate ----------

async def _generate_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, ps: list[int]) -> int:
    chat_id = update.effective_chat.id
    c: sheets.ClassEntry = context.user_data.get("laporan_class")
    if c is None:
        await update.effective_message.reply_text("Sesi kadaluarsa. Ketik /laporan lagi.")
        return ConversationHandler.END
    loading = await update.effective_message.reply_text("⏳ Menyusun laporan PDF...")
    err: str | None = None
    fname = _pdf_filename(context.user_data["facilitator"], c.code)
    pdf_bytes = b""
    try:
        # Per-chat lock: serialize generate dengan handler write lain di chat yg sama.
        async with _sheets(context).for_chat(chat_id):
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            data = await _gather_report_data(context, c, ps)
            loop = asyncio.get_running_loop()
            pdf_bytes = await loop.run_in_executor(None, lambda: _build_pdf(data, fname))
    except sheets.SheetsError as exc:
        err = str(exc)
    except Exception as exc:  # noqa: BLE001 — beta fail-open: laporan gagal, jangan crash
        log.exception("laporan generate failed for %s/%s", chat_id, c.code)
        err = "Terjadi kesalahan saat menyusun laporan."
    try:
        await loading.delete()
    except Exception:
        pass
    if err:
        await update.effective_message.reply_text(f"⚠️ {err}")
        return ConversationHandler.END
    try:
        await update.effective_message.reply_document(
            document=pdf_bytes, filename=fname,
            caption=f"📄 Laporan Kinerja Dosen {html.escape(c.code)}")
        usage.log(chat_id, context.user_data["facilitator"], "laporan", c.code,
                  pertemuan=_meeting_range_label(ps))
    except Exception as exc:  # noqa: BLE001 — send gagal, user minta ulang
        log.warning("laporan send failed for %s: %s", chat_id, exc)
        await update.effective_message.reply_text("⚠️ Gagal mengirim PDF. Coba lagi.")
    return ConversationHandler.END


async def _gather_report_data(context: ContextTypes.DEFAULT_TYPE, c: sheets.ClassEntry,
                              ps: list[int]) -> dict:
    """Join zoom_entries + tab rekap (group by kode col E) + absen + feedback per pertemuan."""
    s = _sheets(context)
    name = context.user_data["facilitator"]
    entries = context.user_data.get("laporan_entries") or await s.zoom_entries(name)
    kc = c.code.casefold()
    entry_by_p: dict[int, dict] = {}
    for e in entries:
        if e["kode"].casefold() != kc:
            continue
        for n in {int(x) for x in re.findall(r"\d+", e.get("pertemuan", "")) if 1 <= int(x) <= 16}:
            entry_by_p.setdefault(n, e)
    tab = await s.find_rekap_tab(name)
    rows = await s.rekap_rows(tab)
    rekap_by_p: dict[int, list[list[str]]] = {}
    for r in rows[1:]:
        if len(r) <= 6 or r[4].strip().casefold() != kc:
            continue
        for n in {int(x) for x in re.findall(r"\d+", r[6] or "") if 1 <= int(x) <= 16}:
            rekap_by_p.setdefault(n, []).append(r)

    meetings = []
    for p in sorted(set(ps)):
        e = entry_by_p.get(p, {})
        rr = rekap_by_p.get(p, [])
        r0 = rr[0] if rr else []
        date = ""
        dosen = ""
        subject = c.subject
        if e:
            date = e.get("tanggal", "") or ""
            dosen = e.get("dosen", "") or ""
            subject = e.get("subject", "") or subject
        if r0:
            date = date or (r0[1].strip() if len(r0) > 1 else "")
            dosen = dosen or (r0[2].strip() if len(r0) > 2 else "")
            subject = subject or (r0[5].strip() if len(r0) > 5 else "")
        counts = {}
        try:
            counts = await s.absen_counts(c.code, p)
        except sheets.SheetsError:
            counts = {}
        if not counts.get("total") and r0:
            def _int(v, i):
                try:
                    return int(v[i]) if len(v) > i and v[i].strip() else 0
                except (ValueError, IndexError):
                    return 0
            counts = {"total": _int(r0, 14), "hadir": _int(r0, 15), "feedback": _int(r0, 16),
                      "tidak": _int(r0, 17), "belum": _int(r0, 18)}
        fb = None
        if dosen:
            try:
                fb = await s.feedback_counts(c.code, str(p), dosen,
                                             str(counts.get("prodi", "") or ""))
            except Exception:  # noqa: BLE001 — fail-open feedback
                fb = None
        responden = (fb or {}).get("q") if fb else None
        if not responden:
            responden = counts.get("feedback", 0)
        total = counts.get("total", 0)
        rate = (responden / total) if total else (1.0 if responden else 0.0)
        skor = round(5.0 * rate, 2)
        meetings.append({"pertemuan": p, "tanggal": date, "subject": subject, "dosen": dosen,
                         "total": total, "hadir": counts.get("hadir", 0),
                         "feedback": counts.get("feedback", 0), "tidak": counts.get("tidak", 0),
                         "belum": counts.get("belum", 0), "responden": responden, "skor": skor})

    points = [m["skor"] for m in meetings]
    w_resp = [m["responden"] for m in meetings]
    w_hadir = [m["hadir"] for m in meetings]

    def _wmean(vals, w):
        sw = sum(w)
        return round(sum(v * wv for v, wv in zip(vals, w)) / sw, 2) if sw else 0.0

    gabungan = round(sum(points) / len(points), 2) if points else 0.0
    return {
        "nama": name,
        "kode": c.code,
        "subject": c.subject,
        "pertemuan_label": _meeting_range_label([m["pertemuan"] for m in meetings]),
        "responden_total": sum(m["responden"] for m in meetings),
        "cards": [
            ("CSAT Gabungan", gabungan),
            ("Performa", _wmean(points, w_resp)),
            ("Pemahaman", _wmean(points, w_hadir)),
            ("Interaktivitas", points[-1] if points else 0.0),
        ],
        "meetings": meetings,
        "tanggal": datetime.now(usage.WIB).strftime("%d-%m-%Y"),
    }


# ---------- chart + PDF ----------

def _find_font_paths() -> tuple[str, str]:
    """(regular, bold). Utama repo fonts/, fallback font bundel matplotlib (Docker)."""
    regular = _FONT_DIR / "DejaVuSans.ttf"
    bold = _FONT_DIR / "DejaVuSans-Bold.ttf"
    if regular.exists() and bold.exists():
        return str(regular), str(bold)
    try:
        import matplotlib
        from pathlib import Path
        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        return str(base / "DejaVuSans.ttf"), str(base / "DejaVuSans-Bold.ttf")
    except Exception:
        return "", ""


def _make_chart_png(ps: list[int], skors: list[float]) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with _MATPLOTLIB_LOCK:
        fig, ax = plt.subplots(figsize=(7.2, 2.6), dpi=150)
        ax.plot(range(len(skors)), skors, color=_CHART_TEAL, linewidth=2, marker="o",
                markersize=5, markerfacecolor=_CHART_TEAL)
        ax.set_ylim(0, 5)
        ax.set_yticks(range(0, 6))
        ax.set_xticks(range(len(ps)))
        ax.set_xticklabels([f"P{p}" for p in ps], fontsize=9)
        ax.grid(axis="y", alpha=0.25)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for i, v in enumerate(skors):
            ax.annotate(f"{v:.2f}", (i, v), textcoords="offset points", xytext=(0, 6),
                        ha="center", fontsize=8, color=_CHART_TEAL)
        ax.set_ylabel("Skor", fontsize=9)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return buf.getvalue()


class _LaporanPDF:
    """fpdf2 landing: header biru #4558D0, 4 card skor, chart teal, tabel
    kualitatif 3 kolom, footer. Landscape data dibiarkan portrait A4 (tiru contoh)."""

    def __init__(self, nama: str, tanggal: str) -> None:
        from fpdf import FPDF

        class PDF(FPDF):
            def footer(self):  # noqa: N802 (fpdf2 API)
                self.set_y(-16)
                self.set_draw_color(*_HEADER_BLUE)
                self.set_line_width(0.3)
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.set_y(-13)
                self.set_font("DejaVuSans", size=8)
                self.set_text_color(120, 120, 120)
                half = (self.w - self.l_margin - self.r_margin) / 2
                self.cell(half, 5, f"Dibuat oleh {nama}", new_x="LMARGIN", new_y="NEXT", align="L")
                self.set_x(self.l_margin)
                self.cell(half, 5, f"Laporan Kinerja Dosen · Cakrawala University · {tanggal}",
                          new_x="END", new_y="NEXT", align="C")
                self.set_x(self.l_margin)
                self.cell(self.w - self.l_margin - self.r_margin, 5,
                          f"Halaman {self.page_no()}/{self.pages_count}", new_x="LMARGIN",
                          new_y="NEXT", align="R")

        self.pdf = pdf = PDF("P", "mm", "A4")
        pdf.set_margins(14, 14, 14)
        pdf.set_auto_page_break(True, 20)
        regular, bold = _find_font_paths()
        pdf.add_font("DejaVuSans", "", regular)
        pdf.add_font("DejaVuSans", "B", bold)

    def _header_band(self, data: dict) -> None:
        pdf = self.pdf
        pdf.add_page()
        pdf.set_fill_color(*_HEADER_BLUE)
        pdf.rect(0, 0, pdf.w, 64, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("DejaVuSans", "B", 18)
        pdf.set_xy(14, 12)
        pdf.cell(pdf.w - 28, 10, "LAPORAN KINERJA DOSEN", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("DejaVuSans", "B", 13)
        pdf.cell(pdf.w - 28, 7, data["nama"], new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("DejaVuSans", "", 11)
        pdf.cell(pdf.w - 28, 6, data["kode"], new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(pdf.w - 28, 6, data["subject"], new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(pdf.w - 28, 6, f"Pertemuan {data['pertemuan_label']}",
                 new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(pdf.w - 28, 6,
                 f"{data['tanggal']} · {data['responden_total']} responden",
                 new_x="LMARGIN", new_y="NEXT", align="C")

    def _cards(self, data: dict) -> None:
        pdf = self.pdf
        pdf.set_y(74)
        pdf.set_text_color(60, 60, 60)
        pdf.set_font("DejaVuSans", "B", 12)
        pdf.cell(pdf.w - 28, 7, "Skor Laporan CSAT (skala 1-5)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_y(pdf.get_y() + 2)
        cw = (pdf.w - pdf.l_margin - pdf.r_margin - 6) / 2
        ch = 26
        for i, (label, score) in enumerate(data["cards"]):
            x = pdf.l_margin + (i % 2) * (cw + 6)
            y = pdf.get_y() + (i // 2) * (ch + 6)
            pdf.set_draw_color(*_HEADER_BLUE)
            pdf.set_line_width(0.5)
            pdf.rect(x, y, cw, ch, style="D", round_corners=True, corner_radius=3)
            pdf.set_font("DejaVuSans", "", 9)
            pdf.set_text_color(90, 90, 90)
            pdf.set_xy(x, y + 4)
            pdf.cell(cw, 5, label, new_x="LMARGIN", new_y="NEXT", align="C")
            pdf.set_font("DejaVuSans", "B", 16)
            pdf.set_text_color(*_HEADER_BLUE)
            pdf.cell(cw, 9, f"{score:.2f}", new_x="LMARGIN", new_y="NEXT", align="C")
            pdf.set_font("DejaVuSans", "", 9)
            pdf.set_text_color(60, 60, 60)
            pdf.cell(cw, 5, _predicate(score), new_x="LMARGIN", new_y="NEXT", align="C")

    def _chart(self, data: dict) -> None:
        pdf = self.pdf
        pdf.set_y(pdf.get_y() + 8)
        pdf.set_text_color(60, 60, 60)
        pdf.set_font("DejaVuSans", "B", 12)
        pdf.cell(pdf.w - 28, 7, "Tren Skor per Pertemuan", new_x="LMARGIN", new_y="NEXT")
        ps = [m["pertemuan"] for m in data["meetings"]]
        skors = [m["skor"] for m in data["meetings"]]
        png = _make_chart_png(ps, skors)
        pdf.image(io.BytesIO(png), pdf.l_margin, pdf.get_y() + 2, w=pdf.w - 28)

    def _qual_table(self, data: dict) -> None:
        pdf = self.pdf
        pdf.add_page()
        pdf.set_text_color(60, 60, 60)
        pdf.set_font("DejaVuSans", "B", 12)
        pdf.cell(pdf.w - 28, 8, "Umpan Balik Kualitatif", new_x="LMARGIN", new_y="NEXT")
        pdf.set_y(pdf.get_y() + 2)
        cols = [(66, "Mata Kuliah"), (58, "Feedback Dosen"), (58, "Tanggal")]
        pdf.set_fill_color(*_HEADER_BLUE)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("DejaVuSans", "B", 10)
        for w, h in cols:
            pdf.cell(w, 8, h, new_x="RIGHT", new_y="TOP", align="C", fill=True)
        pdf.ln(8)
        pdf.set_text_color(40, 40, 40)
        pdf.set_font("DejaVuSans", "", 9)
        for m in data["meetings"]:
            pdf.set_draw_color(210, 210, 210)
            pdf.set_line_width(0.2)
            vals = [m["subject"] or "-", m["dosen"] or "-", m["tanggal"] or "-"]
            pdf.cell(66, 7, vals[0], new_x="RIGHT", new_y="TOP", align="L")
            pdf.cell(58, 7, vals[1], new_x="RIGHT", new_y="TOP", align="L")
            pdf.cell(58, 7, vals[2], new_x="LMARGIN", new_y="NEXT", align="L", border=1)

    def build(self, data: dict, fname: str) -> bytes:
        self._header_band(data)
        self._cards(data)
        self._chart(data)
        self._qual_table(data)
        raw = self.pdf.output()
        return raw.encode("latin-1") if isinstance(raw, str) else bytes(raw)


def _build_pdf(data: dict, fname: str) -> bytes:
    doc = _LaporanPDF(data["nama"], data["tanggal"])
    return doc.build(data, fname)


# ---------- cancel / register ----------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.reply_text("Dibatalkan.")
        except Exception:
            pass
    return ConversationHandler.END


async def back_to_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    name = context.user_data.get("facilitator")
    if not name:
        await q.message.reply_text("Sesi kadaluarsa. Ketik /laporan lagi.")
        return ConversationHandler.END
    try:
        personal, backup, makeup = await _sheets(context).get_all_loggable_classes(name)
    except sheets.SheetsError as exc:
        await q.message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    classes = personal + backup + makeup
    if not classes:
        await q.message.reply_text("Tidak ada kelas untukmu.")
        return ConversationHandler.END
    kb = []
    for i, c in enumerate(classes):
        suffix = f" ({c.backup_hari_tanggal})" if (c.category in ("Backup", "Make-up") and c.backup_hari_tanggal) else f" ({c.day} {c.time_range})"
        icon = "🔄" if c.category == "Backup" else ("🧪" if c.category == "Make-up" else "⭐")
        kb.append([InlineKeyboardButton(f"{icon} {c.code} — {html.escape(c.subject)}{suffix}",
                                        callback_data=f"lpc:{i}")])
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="lpb:cancel")])
    context.user_data["laporan_classes"] = classes
    await q.message.reply_text("1️⃣ Pilih kelas untuk laporan:",
                               reply_markup=InlineKeyboardMarkup(kb))
    return CLASS


async def timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await update.effective_message.reply_text("⏰ Waktu habis. Ketik /laporan lagi.")
    except Exception:
        pass
    return ConversationHandler.END


def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("laporan", cmd_laporan)],
        states={
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^lpc:\d+$"),
                    CallbackQueryHandler(cancel, pattern=r"^lpb:cancel$")],
            MEETING: [CallbackQueryHandler(pick_meeting, pattern=r"^lpq:(all|\d+)$"),
                      CallbackQueryHandler(back_to_class, pattern=r"^lpb:class$"),
                      CallbackQueryHandler(cancel, pattern=r"^lpb:cancel$"),
                      MessageHandler(_SKIP_FILTER, enter_range)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=_TIMEOUT,
        name="laporan_conv",
        allow_reentry=True,
    )
    app.add_handler(conv)