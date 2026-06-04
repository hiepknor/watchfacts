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


def test_parse_listings_extracts_live_watchfacts_product_cards() -> None:
    html = """
    <html>
      <body>
        <div class="product">
          <div class="card shadow-sm">
            <div class="product-image">
              <a href="/flash-sales/1566977?highlight=116500">
                <img src="https://watchfacts.example/image.jpg" />
              </a>
            </div>
            <div class="product-description" id="1566977">
              <a class="title-link" href="/flash-sales/1566977?highlight=116500">
                2021 RLX
                <mark>116500</mark>
                Retail ready
                Watch + Card
                $26 000.00 USD
              </a>
            </div>
            <div class="product-rate-removed">
              <span class="link-dark">
                <span class="blur-premium">Devin Schonauer</span>
              </span>
            </div>
            <span id="countDownText-abc">
              Posted:
              <span class="text-dark">May 5, 2026</span>
            </span>
          </div>
        </div>
      </body>
    </html>
    """

    assert parse_listings(html) == [
        ListingCandidate(
            listing_text="2021 RLX 116500 Retail ready Watch + Card $26 000.00 USD",
            seller="Devin Schonauer",
            posted_date="May 5, 2026",
            image_url="https://watchfacts.example/image.jpg",
            source_url="/flash-sales/1566977?highlight=116500",
        )
    ]


def test_parse_listings_extracts_watchfacts_json_whatsapp_number() -> None:
    html = """
    {
      "listings": [
        {
          "title": "5712G Used 2015 - 76k usdt",
          "companyName": "Issac",
          "companyWhatsapp": "17826241887",
          "whatsappNumber": "17826241887",
          "number": 3074930,
          "createdOn": "2026-06-02 03:18:08",
          "listings": [
            {
              "title": "5712G Used 2015 - 76k usdt",
              "frontImage": "https://watchfacts.example/5712g.jpg"
            }
          ]
        }
      ]
    }
    """

    assert parse_listings(html) == [
        ListingCandidate(
            listing_text="5712G Used 2015 - 76k usdt",
            seller="Issac",
            seller_phone="17826241887",
            posted_date="June 2, 2026",
            image_url="https://watchfacts.example/5712g.jpg",
            source_url="/flash-sales/3074930",
        )
    ]


def test_parse_listings_returns_empty_list_when_no_listing_container_exists() -> None:
    assert parse_listings("<html><body><p>No listings here</p></body></html>") == []
