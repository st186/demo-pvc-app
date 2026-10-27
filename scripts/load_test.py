"""Load test: many PDFs opened and cropped concurrently against a running server.

usage: python scripts/load_test.py SAMPLES_DIR [concurrency] [rounds]

SAMPLES_DIR needs the git-ignored cases.json manifest used by the tests.
Start the server first: python -m pvccard.serve --port 8765
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

URL = "http://localhost:8765"
SAMPLES = Path(sys.argv[1])
CONC = int(sys.argv[2]) if len(sys.argv) > 2 else 10
ROUNDS = int(sys.argv[3]) if len(sys.argv) > 3 else 4

cases = json.loads((SAMPLES / "cases.json").read_text())
jobs = [(SAMPLES / n, c.get("password", "")) for n, c in cases.items()] * ROUNDS


async def one(client, sem, path, pw, stats):
    async with sem:
        data = path.read_bytes()
        t = time.perf_counter()
        try:
            r = await client.post(f"{URL}/api/open", files={"file": (path.name, data)},
                                  data={"password": pw}, timeout=120)
            stats["open"].append(time.perf_counter() - t)
            if r.status_code != 200:
                stats["errors"].append(f"open {path.name}: {r.status_code} {r.text[:80]}")
                return
            o = r.json()
            form = {"password": pw, "front": json.dumps(o["front"]), "dpi": "600"}
            if o["back"]:
                form["back"] = json.dumps(o["back"])
            t = time.perf_counter()
            r = await client.post(f"{URL}/api/crop", files={"file": (path.name, data)}, data=form, timeout=180)
            stats["crop"].append(time.perf_counter() - t)
            if r.status_code != 200:
                stats["errors"].append(f"crop {path.name}: {r.status_code} {r.text[:80]}")
        except Exception as e:  # connection dropped = server crashed
            stats["errors"].append(f"{path.name}: {type(e).__name__} {e}")


async def main():
    stats = {"open": [], "crop": [], "errors": []}
    sem = asyncio.Semaphore(CONC)
    t0 = time.perf_counter()
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(one(client, sem, p, pw, stats) for p, pw in jobs))
    total = time.perf_counter() - t0
    def fmt(xs):
        return f"n={len(xs)} median={statistics.median(xs):.2f}s p95={sorted(xs)[int(len(xs)*.95)-1]:.2f}s max={max(xs):.2f}s" if xs else "n=0"
    print(f"{len(jobs)} documents, concurrency {CONC}, total {total:.1f}s")
    print("open:", fmt(stats["open"]))
    print("crop:", fmt(stats["crop"]))
    print("errors:", len(stats["errors"]))
    for e in stats["errors"][:10]:
        print("  ", e)

asyncio.run(main())
