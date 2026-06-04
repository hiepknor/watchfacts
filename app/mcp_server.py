from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from app.tool_runtime import watchfacts_search_payload


logger = logging.getLogger(__name__)


app = FastMCP("watchfacts")


@app.tool()
async def watchfacts_search(
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("starting watchfacts mcp server on http://0.0.0.0:8765/mcp")
    app.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8765,
        path="/mcp",
    )


if __name__ == "__main__":
    main()
