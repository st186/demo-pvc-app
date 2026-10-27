"""Start PVC Card Studio: python -m pvccard.serve [--host H] [--port P] [--workers N]

On Linux, glibc gives each request thread its own malloc arena and rarely
returns freed memory, which kept ~2 GB resident under load. MALLOC_ARENA_MAX
must be set before the process starts, so this re-executes itself with it
(~800 MB under the same load, same speed).
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    if sys.platform.startswith("linux") and "MALLOC_ARENA_MAX" not in os.environ:
        os.environ["MALLOC_ARENA_MAX"] = "2"
        os.execv(sys.executable, [sys.executable, "-m", "pvccard.serve", *sys.argv[1:]])

    ap = argparse.ArgumentParser(prog="pvccard.serve")
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to accept other machines")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=1, help="processes; pdfium work is serialised per process")
    args = ap.parse_args()

    import uvicorn
    uvicorn.run("pvccard.web:app", host=args.host, port=args.port, workers=args.workers)


if __name__ == "__main__":
    main()
