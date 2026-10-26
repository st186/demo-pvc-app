from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .card import save_card
from .convert import PasswordRequired, convert
from .recipes import RECIPES


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pvccard", description=__doc__)
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("out"))
    ap.add_argument("-p", "--password")
    ap.add_argument("-t", "--type", default="auto", choices=["auto"] + [r.name for r in RECIPES])
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--fit", choices=["stretch", "contain"], default="stretch")
    ap.add_argument("--cmyk", action="store_true", help="save JPEGs in CMYK")
    args = ap.parse_args(argv)

    failed = False
    for path in args.inputs:
        try:
            card = convert(path, args.password, args.type, args.dpi)
        except (PasswordRequired, ValueError) as e:
            print(f"{path.name}: {e}", file=sys.stderr)
            failed = True
            continue
        files = save_card(card, args.out, path.stem, args.dpi, args.fit, args.cmyk)
        print(f"{path.name} [{card.doc_type}] -> " + ", ".join(f.name for f in files))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
