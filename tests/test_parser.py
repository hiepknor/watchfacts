from pathlib import Path

from app.parser import ListingCandidate, parse_listings


FIXTURE = Path(__file__).parent / "fixtures" / "watchfacts_listing.html"


def test_parse_listings_extracts_candidates_from_fixture_html() -> None:
    listings = parse_listings(FIXTURE.read_text())

    assert listings[0] == ListingCandidate(
        listing_text="Rolex 228253A choco N2 467000hkd",
        seller="HK STOCKS",
        posted_date="April 20, 2026",
        image_url="https://watchfacts.example/images/228253a.jpg",
        source_url="https://watchfacts.example/listing/1",
    )


def test_parse_listings_handles_missing_fields_gracefully() -> None:
    listings = parse_listings(FIXTURE.read_text())

    assert listings[1] == ListingCandidate(
        listing_text="Patek 5712 blue dial",
        seller=None,
        posted_date=None,
        image_url=None,
        source_url=None,
    )


def test_parse_listings_returns_empty_list_when_no_listing_container_exists() -> None:
    assert parse_listings("<html><body><p>No listings here</p></body></html>") == []
