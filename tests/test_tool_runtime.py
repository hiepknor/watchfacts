from __future__ import annotations

import asyncio

import pytest

from app.search_result import SearchResult
from app.tool_runtime import watchfacts_search_payload


class FakeWorkflow:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return self.results


def test_watchfacts_search_payload_serializes_results_for_tool_runtime() -> None:
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used 2015 - 76k usdt",
                seller="Issac",
                seller_phone="17826241887",
                raw_listing_text="raw listing text",
                similar_results=(SearchResult("5712G similar"),),
            ),
            SearchResult("5712R 2016 HKD 830000"),
        ]
    )

    payload = asyncio.run(
        watchfacts_search_payload(
            " 5712g ",
            workflow=workflow,
            limit=1,
            include_similar=False,
            include_raw=False,
        )
    )

    assert workflow.queries == ["5712g"]
    assert payload == {
        "query": "5712g",
        "total_count": 2,
        "result_count": 1,
        "truncated": True,
        "results": [
            {
                "listing_text": "5712G Used 2015 - 76k usdt",
                "seller": "Issac",
                "posted_date": None,
                "image_url": None,
                "source_url": None,
                "seller_phone": "17826241887",
                "similar_results": [],
            }
        ],
    }


def test_watchfacts_search_payload_can_include_raw_and_similar_results() -> None:
    workflow = FakeWorkflow(
        [
            SearchResult(
                listing_text="5712G Used",
                raw_listing_text="raw listing text",
                similar_results=(SearchResult("5712G similar"),),
            )
        ]
    )

    payload = asyncio.run(
        watchfacts_search_payload(
            "5712g",
            workflow=workflow,
            include_similar=True,
            include_raw=True,
        )
    )

    assert payload["results"] == [
        {
            "listing_text": "5712G Used",
            "seller": None,
            "posted_date": None,
            "image_url": None,
            "source_url": None,
            "seller_phone": None,
            "similar_results": [
                {
                    "listing_text": "5712G similar",
                    "seller": None,
                    "posted_date": None,
                    "image_url": None,
                    "source_url": None,
                    "seller_phone": None,
                    "similar_results": [],
                    "raw_listing_text": None,
                }
            ],
            "raw_listing_text": "raw listing text",
        }
    ]


def test_watchfacts_search_payload_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="query must not be empty"):
        asyncio.run(watchfacts_search_payload(" ", workflow=FakeWorkflow([])))


def test_watchfacts_search_payload_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        asyncio.run(
            watchfacts_search_payload("5712g", workflow=FakeWorkflow([]), limit=0)
        )
