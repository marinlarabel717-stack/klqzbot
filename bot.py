from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    src_path = root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    from klqzbot.main import async_main, build_parser

    parser = build_parser()
    if len(sys.argv) <= 1:
        args = parser.parse_args(["mirror"])
    else:
        args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
