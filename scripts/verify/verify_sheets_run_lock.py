"""Stub verify: _run lock-acquire timeout + capped drain + normal path + no deadlock.

Speeds the 90/60/30s wall-clock caps by a SCALE factor via a thin asyncio.wait_for
wrapper — real _run code path (wait_for cancellation, drain, finally release)
runs unchanged. Exits via os._exit because the stuck executor thread is non-daemon.
"""
import asyncio
import os
import sys
import time

import asyncio as _aio

SCALE = 10.0
_real_wait_for = _aio.wait_for

async def _scaled_wait_for(awaitable, timeout):
    return await _real_wait_for(awaitable, timeout / SCALE)

# Patch where sheets.py resolves it: `asyncio.wait_for` module attribute.
_aio.wait_for = _scaled_wait_for

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets  # noqa: E402

client = object.__new__(sheets.SheetsClient)
client._lock = asyncio.Lock()
client._gc = None
client.cfg = None  # _run never touches cfg

PASS = []

def check(name, cond):
    if not cond:
        print(f"FAIL: {name}")
        os._exit(1)
    PASS.append(name)
    print(f"ok: {name}", flush=True)

async def main():
    # 1. normal path returns value, lock released
    r = await client._run(lambda: 42)
    check("normal path returns value", r == 42)
    check("normal path releases lock", client._lock.locked() is False)

    # 2. lock busy > acquire cap -> clear SheetsError, nothing leaked
    holder = asyncio.create_task(client._lock.acquire())
    await holder
    try:
        await client._run(lambda: 1)
        check("lock-busy raised", False)
    except sheets.SheetsError as e:
        check("lock-busy message clear", "Google sibuk" in str(e))
    # lock must STILL be owned by holder (our timed-out acquire must not leak)
    check("lock not leaked by failed acquire", client._lock.locked() is True)
    client._lock.release()
    check("lock free after holder release", client._lock.locked() is False)

    # 3. stuck fn: 60s call cap + 30s drain cap -> SheetsError, lock RELEASED after
    def stuck():
        time.sleep(10_000)

    # make it reliably stuck: never returns
    t0 = time.monotonic()
    try:
        await client._run(stuck)
        check("stuck fn raised", False)
    except sheets.SheetsError as e:
        check("stuck fn timeout message", "Sheets timeout" in str(e))
    dur = time.monotonic() - t0
    check("call+drain caps fired (scaled ~9s)", 4 < dur < 40)

    # 4. no self-deadlock: next call succeeds immediately because lock released
    r = await client._run(lambda: 7)
    check("post-timeout call succeeds", r == 7)
    check("post-timeout lock free", client._lock.locked() is False)

    print("\nALL PASS:", ", ".join(PASS))
    os._exit(0)

if __name__ == "__main__":
    asyncio.run(main())