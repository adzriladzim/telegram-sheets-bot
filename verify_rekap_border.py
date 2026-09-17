"""verify_rekap_border.py — READ-ONLY probe of rekap tab separator borders.

Sends NO value/format writes. Inspects userEnteredFormat.border across B..W of a
real per-fasil tab via the Sheets API (includeGridData + fields) using gspread's
http_client, then prints every heavy-border (separator) row detected.

Note: selecting `.border` in the fields picker 400s (Google quirk — Border/
Shading contain a oneof colorStyle); we select full `userEnteredFormat` instead
and read border locally.

Run: py verify_rekap_border.py
"""
from __future__ import annotations

from config import load_config
import sheets

HEAVY = {"SOLID_THICK", "SOLID_MEDIUM", "DOUBLE"}
W_MIN = 4  # width threshold fallback; default border width is 1


def _cell_border_hits(row_idx, uef):
    """[(col_letter, side, style, width)] for border-bearing cells B..W."""
    vals = uef or []
    out = []
    for cidx, cell in enumerate(vals[:23]):  # B(1)..W(22)
        if not isinstance(cell, dict):
            continue
        b = (cell.get("userEnteredFormat") or {}).get("borders") or {}
        for side in ("top", "bottom"):
            eb = b.get(side)
            if not eb:
                continue
            col = "ABCDEFGHIJKLMNOPQRSTUVW"[cidx]
            out.append((col, side, eb.get("style", ""), eb.get("width", 0)))
    return out


def main() -> None:
    cfg = load_config()
    c = sheets.SheetsClient(cfg)
    tab = c._find_rekap_tab(cfg.facilitator_name)
    ws = c._sheet_in(cfg.rekap_sheet_id, tab)
    resp = c._client().http_client.spreadsheets_get(
        cfg.rekap_sheet_id,
        params={
            "ranges": f"{sheets._q(tab)}!A1:W{ws.row_count}",
            "includeGridData": "true",
            "fields": "sheets.data.rowData.values.userEnteredFormat",
        },
    )
    rows = resp["sheets"][0]["data"][0].get("rowData", [])
    print(f"tab={tab!r} grid_rows={ws.row_count} rowData_len={len(rows)}")

    any_border = []
    for i, row in enumerate(rows):
        hits = _cell_border_hits(i, row.get("values") or [])
        if hits:
            any_border.append((i, hits))
    print(f"rows with ANY border in B..W: {len(any_border)}")
    for i, hits in any_border:
        detail = ", ".join(f"{col}:{side}:{st}:w{w}" for col, side, st, w in hits[:14])
        print(f"  row {i + 1}: {detail}")

    heavy = [i for i, hits in any_border
             if any(st in HEAVY or w >= W_MIN for _, _, st, w in hits)]
    print(f"HEAVY border separator rows (detected): {[i + 1 for i in heavy]}")

    # Block-gap separators: empty B..L row directly below a data row.
    vals = ws.get_all_values()
    gap = [i for i in range(1, len(vals))
           if not any((c or "").strip() for c in vals[i][1:12])
           and any((c or "").strip() for c in vals[i - 1][1:12])]
    print(f"block-gap empty rows: {[i + 1 for i in gap]}")
    print(f"combined separator rows: {sorted({i for i in heavy + gap})}")

    if hasattr(c, "_separator_rows"):
        got = c._separator_rows(cfg.rekap_sheet_id, tab)
        print("_separator_rows() ->", [i + 1 for i in got])


if __name__ == "__main__":
    main()