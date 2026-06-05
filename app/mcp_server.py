from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from app.tool_runtime import (
    watchfacts_create_chat_draft_payload,
    watchfacts_get_issue_payload,
    watchfacts_health_payload,
    watchfacts_list_issues_payload,
    watchfacts_report_issue_payload,
    watchfacts_search_payload,
    watchfacts_suspicious_summary_payload,
    watchfacts_update_issue_payload,
)


logger = logging.getLogger(__name__)


app = FastMCP(
    "watchfacts",
    host="0.0.0.0",
    port=8765,
    streamable_http_path="/mcp",
)


@app.tool(
    name="search",
    description="Search WatchFacts products and list matching SKUs.",
)
async def search(
    query: str,
    limit: int = 5,
    include_similar: bool = True,
) -> dict[str, object]:
    """Search WatchFacts and return a structured payload."""
    return await watchfacts_search_payload(
        query=query,
        limit=limit,
        include_similar=include_similar,
        include_raw=False,
    )


@app.tool(
    name="health",
    description="Check WatchFacts search runtime, browser session, database, and OpenWA readiness.",
)
async def health() -> dict[str, object]:
    """Check whether WatchFacts runtime dependencies are ready."""
    return await watchfacts_health_payload()


@app.tool(
    name="create_chat_draft",
    description="Create an OpenWA chat draft for a WatchFacts search result.",
)
async def create_chat_draft(query: str, result_id: str) -> dict[str, object]:
    """Create an OpenWA chat draft from a prior search result."""
    return await watchfacts_create_chat_draft_payload(
        query=query,
        result_id=result_id,
    )


@app.tool(
    name="report_issue",
    description="Report a missing-info or wrong-result issue for a WatchFacts search result.",
)
async def report_issue(
    query: str,
    result_id: str,
    reason: str,
    notes: str | None = None,
) -> dict[str, object]:
    """Record result feedback for owner review."""
    return await watchfacts_report_issue_payload(
        query=query,
        result_id=result_id,
        reason=reason,
        notes=notes,
    )


@app.tool(
    name="list_issues",
    description="List open WatchFacts feedback and suspicious QA issues.",
)
def list_issues(
    issue_type: str = "all",
    limit: int = 10,
    min_severity: int | None = None,
) -> dict[str, object]:
    """List open WatchFacts issue queue items."""
    return watchfacts_list_issues_payload(
        issue_type=issue_type,
        limit=limit,
        min_severity=min_severity,
    )


@app.tool(
    name="get_issue",
    description="Get one WatchFacts issue by reference such as F1 or S1.",
)
def get_issue(issue_ref: str, issue_type: str | None = None) -> dict[str, object]:
    """Get one feedback or suspicious issue."""
    return watchfacts_get_issue_payload(
        issue_ref=issue_ref,
        issue_type=issue_type,
    )


@app.tool(
    name="update_issue",
    description="Mark a WatchFacts issue as open, fixed, or ignored.",
)
def update_issue(
    issue_ref: str,
    status: str,
    notes: str | None = None,
    issue_type: str | None = None,
) -> dict[str, object]:
    """Update feedback or suspicious issue status."""
    return watchfacts_update_issue_payload(
        issue_ref=issue_ref,
        status=status,
        notes=notes,
        issue_type=issue_type,
    )


@app.tool(
    name="suspicious_summary",
    description="Summarize open WatchFacts auto-QA suspicious issues.",
)
def suspicious_summary(limit: int = 20) -> dict[str, object]:
    """Summarize suspicious issue backlog by reason and severity."""
    return watchfacts_suspicious_summary_payload(limit=limit)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("starting watchfacts mcp server on http://0.0.0.0:8765/mcp")
    app.run(transport="streamable-http")


if __name__ == "__main__":
    main()
