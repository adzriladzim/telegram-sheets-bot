"""Repro /stats always-path live (read-only). Run: py verify_stats_path.py"""
from __future__ import annotations
import asyncio, sys, traceback
from collections import Counter, defaultdict
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets, usage
from config import load_config
import handlers.stats as stats

async def main():
    cfg = load_config()
    sc = sheets.SheetsClient(cfg)
    names = sorted(users_dict().values())
    print(f"names={len(names)}")

    # 1. usage
    data, _, _ = usage.stats()
    print("usage.stats ok:", len(data))
    days = 7
    pdata = stats._filter_period(data, days)
    per_menu = Counter(e["action"] for e in pdata)
    per_user = defaultdict(Counter)
    last_ts = {}
    for e in pdata:
        per_user[e["name"]][e["action"]] += 1
        last_ts[e["name"]] = e["ts"]
    print("pdata ok:", len(pdata))

# 2. weekly sections (wrapped in prod but exec anyway)
    matriks, arrears = [], []
    try:
        matriks, arrears = await stats._weekly_sections(sc, names)
        print("weekly ok:", len(matriks), "matriks,", len(arrears), "arrears")
    except Exception as e:
        print("WEEKLY CRASH:", type(e).__name__, e)
        traceback.print_exc()

    # 3. absen coverage
    try:
        cov = await sc.absen_coverage()
        print("absen_coverage ok:", len(cov), "kode")
    except Exception as e:
        print("ABSEN_COVERAGE CRASH:", type(e).__name__, e)
        traceback.print_exc()
        cov = None

# 4. build report — summary + detail
    try:
        L = stats._build_summary(names, data, pdata, days, per_menu, per_user, last_ts, matriks, arrears, cov)
        print("summary ok:", len(L), "lines")
        for i, (part, pm) in enumerate(stats._chunks(L)):
            print(f"  chunk{i} chars={len(part)} bytes={stats._b(part)} plain={pm is None}")
        d = stats._build_detail(names, data, pdata, days, per_menu, per_user, last_ts, matriks, arrears, cov)
        print("detail ok:", len(d), "lines")
        for i, (part, pm) in enumerate(stats._chunks(d)):
            print(f"  chunk{i} chars={len(part)} bytes={stats._b(part)} plain={pm is None}")
    except Exception as e:
        print("BUILD CRASH:", type(e).__name__, e)
        traceback.print_exc()

def users_dict():
    import json
    from config import BASE_DIR
    raw = json.loads((BASE_DIR / "data" / "users.json").read_text(encoding="utf-8"))
    return {int(k): str(v).strip() for k, v in raw.items() if str(v).strip()}

asyncio.run(main())
