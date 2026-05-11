from dataclasses import dataclass

from app.dedupe import dedupe_key, latest_dedupe_key, unique_latest_listings, unique_listings


@dataclass(frozen=True)
class Listing:
    listing_text: str
    seller: str | None = None
    posted_date: str | None = None


def test_dedupe_key_uses_normalized_text_seller_and_posted_date() -> None:
    assert dedupe_key(
        "  Rolex, 228253A   CHOCO ",
        seller=" HK STOCKS ",
        posted_date="April 20, 2026",
    ) == "rolex 228253a choco|hk stocks|april 20 2026"


def test_unique_listings_removes_repeated_normalized_rows_deterministically() -> None:
    first = Listing("Rolex 228253A choco", "HK STOCKS", "April 20, 2026")
    duplicate = Listing(" rolex, 228253a   CHOCO! ", "hk stocks", "April 20 2026")
    different_seller = Listing("Rolex 228253A choco", "Other Seller", "April 20, 2026")

    assert unique_listings([first, duplicate, different_seller]) == [
        first,
        different_seller,
    ]


def test_unique_listings_treats_missing_fields_consistently() -> None:
    first = Listing("Patek 5712 blue", None, None)
    duplicate = Listing("Patek, 5712 blue", "", "")

    assert unique_listings([first, duplicate]) == [first]


def test_latest_dedupe_key_ignores_posted_date() -> None:
    assert latest_dedupe_key("Rolex 228253A choco", seller="HK STOCKS") == (
        "rolex 228253a choco|hk stocks"
    )


def test_unique_latest_listings_keeps_newest_post_per_seller_and_product() -> None:
    older = Listing("7118/1200A blue N2/2026y 725k hkd", "Forest", "April 22, 2026")
    newest = Listing("7118/1200A blue N2/2026y 725k hkd", "Forest", "April 23, 2026")
    other_seller = Listing("7118/1200A blue N2/2026y 725k hkd", "Umi", "April 22, 2026")

    assert unique_latest_listings([older, other_seller, newest]) == [
        newest,
        other_seller,
    ]


def test_unique_latest_listings_handles_reposted_suffix() -> None:
    older = Listing("228235A EIS $541000 N3", "Liu", "April 15, 2026 · Reposted 2x")
    newer = Listing("228235A EIS $541000 N3", "Liu", "April 16, 2026")

    assert unique_latest_listings([older, newer]) == [newer]


def test_unique_latest_listings_keeps_same_seller_product_with_different_price() -> None:
    older_lower_price = Listing(
        "7118/1200A blue N2/2026y 725k hkd",
        "Forest",
        "April 22, 2026",
    )
    newer_higher_price = Listing(
        "7118/1200A blue N2/2026y 735k hkd",
        "Forest",
        "April 23, 2026",
    )

    assert unique_latest_listings([older_lower_price, newer_higher_price]) == [
        older_lower_price,
        newer_higher_price,
    ]
