from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ai_refiner import (
    deterministic_refine_listing_text,
)
from app.matcher import extract_relevant_listing_text


CASES = [
    {
        "name": "fpj_multi_item_choose_elegante",
        "query": "Fpj Elegante Titanium",
        "text": (
            "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
            "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
            "FPJ Rose Gold CS opendate watch with card $130,000USD - [ ] Greubel Forsey"
        ),
        "must": ["Elegante", "Titanium", "120,000usd"],
        "must_not": ["quantieme", "Rose Gold", "Greubel"],
    },
    {
        "name": "fpj_trailing_unrelated_brand",
        "query": "Fpj Elegante Titanium",
        "text": "FPJ Elegante titanium 48mm 2019 fullset HKD785K tudor",
        "must": ["Elegante", "HKD785K"],
        "must_not": ["tudor"],
    },
    {
        "name": "ap_keycap_emoji_price",
        "query": "26240ba new 2024",
        "text": "26240BA new 2024 💎💫👑✨ $8️⃣0️⃣k member 932184 March 25, 2026",
        "must": ["26240BA", "2024"],
        "must_not": ["member 932184"],
    },
    {
        "name": "patek_bundle_choose_7118",
        "query": "7118/1200a blue",
        "text": (
            "124200 pistachio $60000 N12 • 126303g black oys $128000 N8 • "
            "7118/1200A blue N2/2026y 725k hkd • 7300/1200R white 03/2026 $366k • "
            "5726/1A blue N9/2025y 1.065m hkd"
        ),
        "must": ["7118/1200A", "blue", "725k"],
        "must_not": ["124200", "7300/1200R", "5726/1A"],
    },
    {
        "name": "patek_variant_5726",
        "query": "5726/1a",
        "text": (
            "PP 7130G-016 Paper of 2022 USD31000 "
            "PP7010G-013, 2025 model, full set price: US$63,000 "
            "5726/1A-014 2021 Full Set: US$115,000"
        ),
        "must": ["5726/1A-014", "US$115,000"],
        "must_not": ["7130G", "7010G"],
    },
    {
        "name": "5990_spacing_currency",
        "query": "5990/1r",
        "text": (
            "5990/1R Blue dial Mint condition Complete set $255 000.00 USD | "
            "5990/1r - 2022 - German Paper - 217,5€ | Rolex Daytona 116500 panda $30,950"
        ),
        "must": ["5990/1"],
        "must_not": ["116500", "Daytona"],
    },
]


def _case_passed(case: dict[str, object], refined: str) -> bool:
    lowered = refined.casefold()
    return all(
        str(term).casefold() in lowered for term in case["must"]  # type: ignore[index]
    ) and not any(
        str(term).casefold() in lowered for term in case["must_not"]  # type: ignore[index]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark hard WatchFacts text cases.")
    args = parser.parse_args()

    passed = 0
    total_start = time.perf_counter()
    print(f"mode=deterministic cases={len(CASES)}")
    for case in CASES:
        start = time.perf_counter()
        extracted = extract_relevant_listing_text(str(case["query"]), str(case["text"]))
        refined = deterministic_refine_listing_text(
            str(case["query"]),
            extracted,
        )
        elapsed = time.perf_counter() - start
        ok = _case_passed(case, refined)
        passed += int(ok)
        print(f"CASE {case['name']} ok={ok} elapsed={elapsed:.2f}s")
        print(f"  refined={refined}")

    print(f"SUMMARY passed={passed}/{len(CASES)} elapsed={time.perf_counter() - total_start:.2f}s")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
