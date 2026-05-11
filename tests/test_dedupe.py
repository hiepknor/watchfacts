from dataclasses import dataclass

from app.dedupe import dedupe_key, unique_listings


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
