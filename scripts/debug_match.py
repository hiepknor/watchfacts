from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    from app.match_debug import format_match_debug

    parser = argparse.ArgumentParser(
        description="Inspect deterministic matcher trace and result score.",
    )
    parser.add_argument("query")
    parser.add_argument("listing_text")
    parser.add_argument("--posted-date")
    parser.add_argument("--raw-listing-text")
    args = parser.parse_args()

    print(
        format_match_debug(
            args.query,
            args.listing_text,
            posted_date=args.posted_date,
            raw_listing_text=args.raw_listing_text,
        )
    )


if __name__ == "__main__":
    main()
