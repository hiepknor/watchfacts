from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import app.result_pages as result_pages
from app.result_pages import (
    ResultPageConfig,
    generate_result_page,
    read_result_page_action_payload,
    read_result_page_html,
    render_result_page_template,
)
from app.search_result import SearchResult


def make_config(tmp_path, *, public_base_url: str = "https://mcp.example/results"):
    return ResultPageConfig(
        public_base_url=public_base_url,
        ttl_seconds=60,
        max_results=1,
        storage_dir=tmp_path / "pages",
        watchfacts_url="https://watchfacts.example/simon-match-making",
    )


def chrome_executable() -> str | None:
    candidates = [
        os.environ.get("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for root in (
        Path("/ms-playwright"),
        Path.home() / ".cache" / "ms-playwright",
    ):
        candidates.extend(
            str(path)
            for pattern in (
                "chromium-*/chrome-linux/chrome",
                "chromium-*/chrome-linux64/chrome",
                "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
            )
            for path in sorted(root.glob(pattern))
        )
    return next((candidate for candidate in candidates if candidate and os.path.exists(candidate)), None)


def test_render_result_page_template_defaults_to_null_results() -> None:
    html = render_result_page_template()

    assert "let results = null;" in html


def test_result_page_template_is_loaded_from_project_template_file() -> None:
    source = Path(result_pages.__file__).read_text(encoding="utf-8")
    template = result_pages.RESULT_PAGE_TEMPLATE_PATH.read_text(encoding="utf-8")
    css = result_pages.RESULT_PAGE_CSS_PATH.read_text(encoding="utf-8")
    js = result_pages.RESULT_PAGE_JS_PATH.read_text(encoding="utf-8")
    rendered = render_result_page_template({"query": "5712g", "results": []})

    assert result_pages.RESULT_PAGE_TEMPLATE_PATH.name == "result_page.html"
    assert result_pages.RESULT_PAGE_CSS_PATH.name == "result_page.css"
    assert result_pages.RESULT_PAGE_JS_PATH.name == "result_page.js"
    assert result_pages.RESULT_PAGE_CSS_PLACEHOLDER in template
    assert result_pages.RESULT_PAGE_JS_PLACEHOLDER in template
    assert result_pages.RESULT_PAGE_TEMPLATE_PLACEHOLDER in js
    assert ".result-card" in css
    assert "function renderResults()" in js
    assert "_HTML_TEMPLATE" not in source
    assert result_pages.RESULT_PAGE_TEMPLATE_PLACEHOLDER not in rendered
    assert result_pages.RESULT_PAGE_CSS_PLACEHOLDER not in rendered
    assert result_pages.RESULT_PAGE_JS_PLACEHOLDER not in rendered
    assert "PetiteVue" not in rendered
    assert "v-scope" not in rendered
    assert "v-effect" not in rendered
    assert "[v-cloak]" not in rendered
    assert "https://unpkg.com" not in rendered


def test_render_result_page_template_includes_operational_dashboard_hooks() -> None:
    html = render_result_page_template()

    assert "id=\"resultsMeta\"" in html
    assert "class=\"header-main\"" in html
    assert "summary-item summary-total" in html
    assert "summary-item summary-status" in html
    assert ".header-main" in html
    assert ".meta-chip + .meta-chip" in html
    assert ".summary-item::before" in html
    assert "id=\"densityDense\"" in html
    assert "function renderHeader()" in html
    assert "function renderToolbarState()" in html
    assert "function renderResults()" in html
    assert "function createResultCard(item)" in html
    assert "No result payload" in html
    assert ".view-note" in html
    assert "display: none;" in html


def test_render_result_page_template_includes_product_grid_card_hooks() -> None:
    html = render_result_page_template()

    assert '"result-media"' in html
    assert '"result-body"' in html
    assert '"result-lead"' in html
    assert '"result-actions-primary"' in html
    assert '"result-details"' in html
    assert 'id="resultModal"' in html
    assert 'aria-modal="true"' in html
    assert "--listing-accent" in html
    assert "repeat(auto-fill, minmax(13.5rem, 1fr))" in html
    assert "repeat(auto-fill, minmax(11rem, 1fr))" in html
    assert "repeat(auto-fill, minmax(18rem, 1fr))" in html
    assert "grid-template-columns: 5.25rem minmax(0, 1fr)" in html
    assert "grid-template-areas: \"media body\"" in html
    assert ".results.density-dense .thumb {\n        min-height: 100%;" in html
    assert ".density-toggle {\n        flex: 1 0 100%;" in html
    assert "flex: 1 1 calc(50% - 0.3rem)" in html
    assert ".results {\n        grid-template-columns: 1fr;\n        gap: 0.55rem;" in html
    assert "repeat(auto-fill, minmax(9.5rem, 1fr))" in html
    assert ".results {\n        grid-template-columns: repeat(2, minmax(0, 1fr));" in html
    assert ".results.density-dense {\n        grid-template-columns: repeat(3, minmax(0, 1fr));" in html
    assert "-webkit-line-clamp: 4" in html
    assert "truncate(item.listing_text, 220)" in html
    assert "scrollbar-gutter: stable;" in html
    assert "--modal-scrollbar-compensation" in html
    assert "body.append(lead, actions);" in html
    assert "article.append(media, body);" in html
    assert "function hasImage(item)" in html
    assert "function createDetailsMeta(item)" in html
    assert 'function createModalActions(item, similarPanel, className = "")' in html
    assert "function createResultDetailsContent(item)" in html
    assert "function formattedListingFields(item)" in html
    assert 'function createFormattedListingDisplay(item, className = "", titleTag = "h2")' in html
    assert "function formatListingCopy(item)" in html
    assert 'option value="price_desc"' in html
    assert 'option value="price_asc"' in html
    assert "formattedListingFields(item).map" in html
    assert "function formatCopyDate(value)" in html
    assert "raw.split(\"·\", 1)[0].split(\" - \", 1)[0].trim()" in html
    assert 'return iso[3] + "/" + iso[2] + "/" + iso[1];' in html
    assert 'return padDatePart(monthMatch[2]) + "/" + padDatePart(monthNumber) + "/" + monthMatch[3];' in html
    assert "january: 1, jan: 1" in html
    assert 'copyPrefix: "Listing: "' in html
    assert 'value: copyField(item.listing_text, "No listing text")' in html
    assert 'copyPrefix: "Seller: "' in html
    assert "value: copyField(item.seller)" in html
    assert 'copyPrefix: "Date: "' in html
    assert "value: formatCopyDate(item.posted_date)" in html
    assert 'join("\\n");' in html
    assert "function makeFormattedCopyButton(item)" in html
    assert "function focusWithoutScroll(node)" in html
    assert "function lockModalScroll()" in html
    assert "function unlockModalScroll()" in html
    assert "function needsModalScrollbarCompensation()" in html
    assert 'CSS.supports("scrollbar-gutter", "stable")' in html
    assert 'createNode("span", "copy-label", "Copy Text")' in html
    assert 'label.dataset.full = "Copy Text";' in html
    assert 'label.dataset.short = "Copy";' in html
    assert 'label.setAttribute("aria-hidden", "true");' in html
    assert "primary.appendChild(makeFormattedCopyButton(item));" in html
    assert 'makeButton("Copy ID", "Copy result_id"' in html
    assert "Professional results workspace consolidated layer" in html
    assert "--workspace-main" in html
    assert "--modal-width" in html
    assert "--title-box" in html
    assert "grid-template-rows: var(--title-box) var(--meta-box)" in html
    assert "grid-template-rows: minmax(0, 1fr) auto" in html
    assert "grid-template-columns: minmax(0, 1fr) auto" in html
    assert 'idWrap.setAttribute("aria-label", "ID: " + text(item.result_id, "No result_id"));' in html
    assert 'chip.setAttribute("aria-label", value.label + value.value);' in html
    assert ".listing-display-card" in html
    assert ".listing-display-card .listing-line" in html
    assert ".listing-display-card .listing-icon" in html
    assert ".listing-display-card .listing-icon {\n      display: none;" in html
    assert ".has-image .thumb" in html
    assert 'thumb.classList.add("is-missing-image");' in html
    assert 'card.classList.add("no-image", "image-load-failed");' in html
    assert "padding: 0;" in html
    assert "position: relative;" in html
    assert ".thumb img {\n      position: absolute;\n      inset: 0;" in html
    assert "--result-media-mobile" in html
    assert "grid-template-columns: var(--result-media-mobile) minmax(0, 1fr)" in html
    assert ".result-actions-primary,\n      .results.density-dense .result-actions-primary {\n        display: grid;" in html
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in html
    assert "Responsive standardization pass." in html
    assert "Breakpoints: desktop base, tablet 761-1024, mobile 421-760, narrow <=420." in html
    assert "@media (min-width: 761px) and (max-width: 1024px)" in html
    assert "@media (max-width: 760px)" in html
    assert "@media (max-width: 420px)" in html
    assert "grid-template-columns: repeat(3, minmax(5.25rem, 5.25rem));" in html
    assert ".result-actions-primary .source-link,\n    .results.density-dense .result-actions-primary .source-link" in html
    assert ".result-actions-primary .source-link,\n      .results.density-dense .result-actions-primary .source-link {\n        display: none;" in html
    assert "order: 3;" in html
    assert ".result-actions-primary .action-button:first-child" in html
    assert "order: 1;" in html
    assert "order: 2;" in html
    assert "min-width: 5.25rem;" in html
    assert ".result-card.no-image .thumb,\n      .result-card.image-load-failed .thumb," in html
    assert "place-items: center;" in html
    assert 'grid-template-areas: "body";' not in html
    assert ".result-id-icon" in html
    assert ".modal-heading" in html
    assert ".modal-section" in html
    assert ".modal-listing-section" in html
    assert ".modal-meta-section" in html
    assert ".modal-actions-section" in html
    assert ".modal-actions" in html
    assert ".modal-similar-section" in html
    assert "grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr)" in html
    assert "position: sticky" in html
    assert ".listing-line-title .listing-value" in html
    assert ".listing-display-detail .listing-line-title" in html
    assert ".listing-display-detail .listing-line-title .listing-icon" in html
    assert "grid-template-columns: minmax(4.55rem, auto) minmax(0, 1fr)" in html
    assert "grid-template-columns: minmax(3.7rem, auto) minmax(0, 1fr)" in html
    assert ".listing-display-detail .listing-line-meta" in html
    assert "border-radius: 999px" in html
    assert "box-shadow: 0 1px 0 rgba(17, 24, 23, 0.03)" in html
    assert 'listingSection.appendChild(hero);' in html
    assert 'mediaCard.appendChild(createThumb(item));' in html
    assert "createModalQuickFacts" not in html
    assert "modal-quick-facts" not in html
    assert 'createFormattedListingDisplay(item, "listing-display-detail", "div")' in html
    assert 'row.appendChild(createFormattedListingDisplay(similarItem, "listing-display-similar", "div"));' in html
    assert 'const listingDisplay = createFormattedListingDisplay(item, "listing-display-card");' in html
    assert "lead.appendChild(listingDisplay);" in html
    assert ".copy-label::after" in html
    assert "content: attr(data-full)" in html
    assert "content: attr(data-short)" in html
    assert ".toolbar-actions {\n        flex: 1 0 100%;\n        min-width: 0;\n        flex-wrap: wrap;" in html
    assert "overflow-x: auto" not in html
    assert '"result-rail"' not in html
    assert '"row-actions"' not in html
    assert '"has-media-slot"' not in html
    assert '"media body actions"' not in html
    assert "article.append(media, body, details);" not in html
    assert '"result-meta"' not in html
    assert "function primaryMeta(item)" not in html
    assert 'primary.appendChild(makeButton("ID"' not in html
    assert 'primary.appendChild(makeButton("Text"' not in html
    assert "\U0001f194" not in html
    assert "\u260e" not in html
    assert "\U0001f517" not in html
    assert "\U0001f3f7" not in html
    assert "\U0001f464" not in html
    assert "\U0001f4c5" not in html
    assert 'const title = createNode("h2", "listing-title", titleFromListing(item));' not in html
    assert 'details.appendChild(createNode("p", "detail-listing", text(item.listing_text, "No listing text")));' not in html
    assert '"copy-label-full"' not in html
    assert '"copy-label-short"' not in html
    assert 'makeButton("Copy", "Copy result_id"' not in html
    assert 'secondary.appendChild(makeButton("Copy Text"' not in html


def test_render_result_page_template_wires_result_details_modal_behavior() -> None:
    html = render_result_page_template()

    assert '<div class="result-modal" id="resultModal" role="dialog" aria-modal="true" aria-labelledby="resultModalTitle" hidden>' in html
    assert 'detailsToggle.setAttribute("aria-haspopup", "dialog")' in html
    assert 'detailsToggle.setAttribute("aria-controls", "resultModal")' in html
    assert 'openDetailsModal(item, detailsToggle)' in html
    assert "function openDetailsModal(item, trigger)" in html
    assert "function closeDetailsModal(options = {})" in html
    assert "function handleModalKeydown(event)" in html
    assert "function modalTitle(item)" in html
    assert "els.modalTitle.textContent = modalTitle(item);" in html
    assert "els.modalBody.replaceChildren(createResultDetailsContent(item));" in html
    assert "lockModalScroll();" in html
    assert "focusWithoutScroll(els.modalClose);" in html
    assert "unlockModalScroll();" in html
    assert "focusWithoutScroll(lastModalTrigger);" in html
    assert 'return "WatchFacts listing";' in html
    assert "createModalFact" not in html
    assert 'class="modal-fact-label"' not in html
    assert 'Report missing details or a wrong match for review.' in html
    assert 'function createOpenWaDraftAction(item)' not in html
    assert 'function createReportIssueForm(item)' in html
    assert 'function postResultAction(url, item, extra = {})' in html
    assert 'submit.textContent = "Submitting...";' in html
    assert 'makeButton("OpenWA", "Create an OpenWA chat draft"' in html
    assert '"Submit report"' in html
    assert 'function createModalQuickFacts(item)' not in html
    assert 'const hero = createNode("div", "modal-overview");' in html
    assert 'const mediaCard = createNode("div", "modal-media-card");' in html
    assert 'mediaCard.classList.add("modal-media-card-no-image")' in html
    assert 'const listingCopy = createNode("div", "modal-listing-copy");' in html
    assert ".modal-media-card .thumb" in html
    assert "grid-template-columns: 7rem minmax(0, 1fr)" not in html
    assert ".modal-media-card {\n        grid-template-columns: 1fr;" in html
    assert "max-height: 7rem" not in html
    assert "max-height: clamp(14rem, 42vh, 19rem)" in html
    assert "aspect-ratio: 4 / 3" in html
    assert "max-height: 5.5rem" not in html
    assert "max-height: 6rem" not in html
    assert "border-right-style: dashed" in html
    assert "position: relative;" in html
    assert 'createNode("div", "modal-section-label", "Listing snapshot")' in html
    assert 'createNode("div", "action-card-title", "OpenWA handoff")' not in html
    assert 'createNode("div", "action-card-title", "Quality feedback")' in html
    assert "const openWaDraftStateByResultId = new Map();" in html
    assert "function createOpenWaDraftButton(item, options = {})" in html
    assert "function handleOpenWaDraft(item)" in html
    assert 'primary.appendChild(createOpenWaDraftButton(item, { compact: true }));' in html
    assert 'button.dataset.openwaActionButton = "true";' in html
    assert 'status: "success",' in html
    assert ".openwa-action" in html
    assert ".openwa-action[disabled]" in html
    assert "display: inline-flex;" in html
    assert 'button.textContent = "Retry";' in html
    assert 'button.textContent = "Retry OpenWA";' not in html
    assert ".action-button[disabled] {\n        display: none;" not in html
    assert ".action-button[disabled]:not(.openwa-action)" in html
    assert '"wrong_result", "Wrong result"' in html
    assert '"missing_info", "Missing info"' in html
    assert 'copyValue: text(item.source_url)' in html
    assert 'makeButton("Copy", "Copy " + value.copyLabel' in html
    assert 'makeButton("Copy OpenWA", "Copy prompt to create an OpenWA chat draft"' not in html
    assert 'makeButton("Copy Report", "Copy prompt to report this result"' in html
    assert '"reason: wrong_result | missing_info | other"' in html
    assert 'const idIcon = createNode("span", "result-id-icon", "ID: ");' in html
    assert 'idWrap.append(idIcon, idText, idCopy);' in html
    assert 'copyLabel: "Source URL"' in html
    assert '"Phone: " + similarItem.seller_phone' in html
    assert '"Source: " + hostName(similarItem.source_url)' in html
    assert 'const listingSection = createNode("section", "modal-section modal-listing-section");' in html
    assert 'const metaSection = createNode("section", "modal-section modal-meta-section");' in html
    assert 'metaSection.appendChild(idWrap);' in html
    assert 'const actionsSection = createNode("section", "modal-actions-section");' in html
    assert 'actionsSection.appendChild(createModalActions(item, similar, "modal-actions"));' in html
    assert 'const dataPanel = createNode("div", "modal-data-panel");' in html
    assert 'const feedbackPanel = createNode("div", "modal-feedback-panel");' in html
    assert 'const workflowSection = createNode("section", "modal-section modal-workflow-section");' in html
    assert 'workflowSection.append(dataPanel, feedbackPanel);' in html
    assert html.index('const metaSection = createNode("section", "modal-section modal-meta-section");') < html.index('const actionsSection = createNode("section", "modal-actions-section");')
    assert html.index('const workflowSection = createNode("section", "modal-section modal-workflow-section");') < html.index('const similarSection = createNode("section", "modal-section modal-similar-section");')
    assert 'const similarSection = createNode("section", "modal-section modal-similar-section");' in html
    assert 'document.body.classList.add("modal-open");' in html
    assert 'document.body.classList.remove("modal-open");' in html
    assert "els.modalClose.focus();" not in html
    assert "lastModalTrigger.focus();" not in html
    assert 'if (event.key === "Escape")' in html
    assert 'if (event.key !== "Tab") return;' in html
    assert 'els.modalClose.addEventListener("click", () => closeDetailsModal());' in html
    assert 'els.modalBackdrop.addEventListener("click", () => closeDetailsModal());' in html
    assert 'document.addEventListener("keydown", handleModalKeydown);' in html
    assert "closeDetailsModal({ restoreFocus: false });" in html
    assert "els.modalTitle.textContent = titleFromListing(item);" not in html


def test_render_result_page_template_normalizes_reposted_dates_for_sort() -> None:
    html = render_result_page_template()

    assert 'split("·"' in html
    assert 'split(" - "' in html


def test_render_result_page_template_browser_behaviors(tmp_path) -> None:
    chrome = chrome_executable()
    if chrome is None:
        pytest.skip("Chrome or Chromium is not installed")

    payload = {
        "query": "116500 panda",
        "created_at": "2026-06-08T04:00:00Z",
        "expires_at": "2026-06-08T05:00:00Z",
        "total_count": 3,
        "offset": 0,
        "limit": 60,
        "next_offset": None,
        "result_count": 3,
        "results": [
            {
                "rank": 1,
                "result_id": "watchfacts-result-001",
                "source_result_id": "watchfacts-result-001",
                "listing_text": "5980/1R Like New. Full set NEW BUCKLE 2022 $210.000",
                "price_reason": "price.visible",
                "seller": "Richie",
                "posted_date": "June 2, 2026",
                "image_url": None,
                "source_url": "https://watchfacts.example/listing/1",
                "seller_phone": "+1 555 10001",
                "similar_results": [],
            },
            {
                "rank": 2,
                "result_id": "watchfacts-result-002",
                "source_result_id": "watchfacts-result-002",
                "listing_text": "116500 panda full set, excellent condition, boxed papers",
                "price_reason": "price.missing_visible",
                "seller": "Seller 2",
                "posted_date": "June 3, 2026 - reposted",
                "image_url": None,
                "source_url": "https://watchfacts.example/listing/2",
                "seller_phone": None,
                "similar_results": [
                    {
                        "listing_text": "Similar 116500 panda alternate listing",
                        "seller": "Similar Seller",
                        "posted_date": "May 17, 2026",
                        "image_url": None,
                        "source_url": "https://watchfacts.example/similar/2",
                        "seller_phone": "+1 555 9000",
                    }
                ],
            },
            {
                "rank": 3,
                "result_id": "watchfacts-result-003",
                "source_result_id": "watchfacts-result-003",
                "listing_text": "5712g should be filterable but older $150,000",
                "price_reason": "price.visible",
                "seller": "Seller 3",
                "posted_date": "May 10, 2026",
                "image_url": None,
                "source_url": None,
                "seller_phone": None,
                "similar_results": [],
            },
        ],
    }
    audit_script = """
    <script>
      setTimeout(() => {
        const output = {};
        try {
          window.__copiedText = [];
          Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { writeText: async value => window.__copiedText.push(value) }
          });

          output.cardCount = document.querySelectorAll(".result-card").length;
          output.query = document.querySelector("#queryText").textContent;
          document.querySelectorAll('button[aria-label="Copy formatted listing text"]')[0].click();

          setTimeout(() => {
            output.copied = window.__copiedText.at(-1);
            document.querySelectorAll('button[aria-label="Show result details"]')[1].click();
            output.modalVisible = !document.querySelector("#resultModal").hidden;
            output.modalTitle = document.querySelector("#resultModalTitle").textContent;
            document.querySelector('button[aria-label="Show similar listings"]').click();
            output.similarButton = document.querySelector('button[aria-label="Hide similar listings"]').textContent;
            output.similarText = document.querySelector(".similar-panel").textContent;

            const sort = document.querySelector("#sortSelect");
            sort.value = "posted_desc";
            sort.dispatchEvent(new Event("change", { bubbles: true }));
            output.sortedFirstRank = document.querySelector(".rank-badge").textContent;

            sort.value = "price_desc";
            sort.dispatchEvent(new Event("change", { bubbles: true }));
            output.priceDescFirstRank = document.querySelector(".rank-badge").textContent;

            sort.value = "price_asc";
            sort.dispatchEvent(new Event("change", { bubbles: true }));
            output.priceAscFirstRank = document.querySelector(".rank-badge").textContent;

            const filter = document.querySelector("#filterInput");
            filter.value = "richie";
            filter.dispatchEvent(new Event("input", { bubbles: true }));
            output.filteredCount = document.querySelectorAll(".result-card").length;
            output.filteredRank = document.querySelector(".rank-badge").textContent;

            filter.value = "";
            filter.dispatchEvent(new Event("input", { bubbles: true }));
            document.querySelector("#densityDense").click();
            output.denseClass = document.querySelector("#resultsList").className;
            document.querySelector("#densityComfortable").click();
            output.comfortableClass = document.querySelector("#resultsList").className;

            const node = document.createElement("pre");
            node.id = "behaviorAudit";
            node.textContent = JSON.stringify(output);
            document.body.appendChild(node);
          }, 0);
        } catch (error) {
          const node = document.createElement("pre");
          node.id = "behaviorAudit";
          node.textContent = JSON.stringify({ error: String(error && error.stack || error) });
          document.body.appendChild(node);
        }
      }, 0);
    </script>
    """
    page_path = tmp_path / "result-page.html"
    page_path.write_text(
        render_result_page_template(payload).replace("</body>", f"{audit_script}</body>"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--virtual-time-budget={os.getenv('RESULT_TEMPLATE_VIRTUAL_TIME_BUDGET', '8000')}",
            "--dump-dom",
            page_path.as_uri(),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=int(os.getenv("RESULT_TEMPLATE_BROWSER_TIMEOUT", "60")),
    )
    match = re.search(r'<pre id="behaviorAudit">([^<]+)</pre>', result.stdout)
    assert match is not None, result.stdout
    behavior = json.loads(html.unescape(match.group(1)))

    assert behavior == {
        "cardCount": 3,
        "query": "116500 panda",
        "copied": (
            "Listing: 5980/1R Like New. Full set NEW BUCKLE 2022 $210.000\n"
            "Seller: Richie\n"
            "Date: 02/06/2026"
        ),
        "modalVisible": True,
        "modalTitle": "Result #2 details",
        "similarButton": "Hide similar",
        "similarText": (
            "Similar listings"
            "Listing: Similar 116500 panda alternate listing"
            "Seller: Similar Seller"
            "Date: 17/05/2026"
            "Phone: +1 555 9000"
            "Source: watchfacts.example"
        ),
        "sortedFirstRank": "#2",
        "priceDescFirstRank": "#1",
        "priceAscFirstRank": "#3",
        "filteredCount": 1,
        "filteredRank": "#1",
        "denseClass": "results density-dense",
        "comfortableClass": "results density-comfortable",
    }


def test_result_page_template_keeps_in_app_browser_header_compact(tmp_path) -> None:
    chrome = chrome_executable()
    if chrome is None:
        pytest.skip("Chrome or Chromium is not installed")

    results = [
        {
            "rank": rank,
            "result_id": f"watchfacts-result-{rank:03d}",
            "source_result_id": f"watchfacts-result-{rank:03d}",
            "listing_text": f"5205R GREEN NEW {rank}/2026 $416,000 HKD",
            "seller": f"Seller {rank}",
            "posted_date": "June 12, 2026",
            "image_url": None,
            "source_url": None,
            "seller_phone": None,
            "similar_results": [],
        }
        for rank in range(1, 7)
    ]
    payload = {
        "query": "5205r green",
        "created_at": "2026-06-13T04:44:00Z",
        "expires_at": "2026-06-14T04:44:00Z",
        "total_count": 26,
        "offset": 0,
        "limit": 60,
        "next_offset": 26,
        "result_count": len(results),
        "results": results,
    }
    audit_script = """
    <script>
      setTimeout(() => {
        const rect = selector => {
          const node = document.querySelector(selector);
          const box = node.getBoundingClientRect();
          return { y: box.y, height: box.height, bottom: box.bottom };
        };
        const commandbarNode = document.querySelector(".commandbar");
        const chips = document.querySelectorAll(".meta-chip");
        const output = {
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          overflowX: document.documentElement.scrollWidth > window.innerWidth,
          commandbarPosition: getComputedStyle(commandbarNode).position,
          header: rect(".page-header"),
          commandbar: rect(".commandbar"),
          firstResult: rect(".result-card"),
          generatedDisplay: getComputedStyle(chips[0]).display,
          expiresDisplay: getComputedStyle(chips[1]).display,
          pageDisplay: getComputedStyle(chips[2]).display
        };
        window.scrollTo(0, 380);
        setTimeout(() => {
          const commandbar = commandbarNode.getBoundingClientRect();
          const visibleCards = Array.from(document.querySelectorAll(".result-card"))
            .map((node, index) => ({ index: index + 1, rect: node.getBoundingClientRect() }))
            .filter(item => item.rect.bottom > 0 && item.rect.top < window.innerHeight);
          output.scrollY = window.scrollY;
          output.commandbarAfterScroll = {
            y: commandbar.y,
            height: commandbar.height,
            bottom: commandbar.bottom
          };
          output.commandbarOverlapsVisibleCard = visibleCards.some(item => (
            commandbar.bottom > item.rect.top && commandbar.y < item.rect.bottom
          ));
          const node = document.createElement("pre");
          node.id = "layoutAudit";
          node.textContent = JSON.stringify(output);
          document.body.appendChild(node);
        }, 0);
      }, 0);
    </script>
    """
    page_path = tmp_path / "result-page-mobile.html"
    page_path.write_text(
        render_result_page_template(payload).replace("</body>", f"{audit_script}</body>"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--force-device-scale-factor=1",
            "--window-size=390,640",
            f"--virtual-time-budget={os.getenv('RESULT_TEMPLATE_VIRTUAL_TIME_BUDGET', '8000')}",
            "--dump-dom",
            page_path.as_uri(),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=int(os.getenv("RESULT_TEMPLATE_BROWSER_TIMEOUT", "60")),
    )
    match = re.search(r'<pre id="layoutAudit">([^<]+)</pre>', result.stdout)
    assert match is not None, result.stdout
    layout = json.loads(html.unescape(match.group(1)))

    assert layout["overflowX"] is False
    assert layout["generatedDisplay"] == "none"
    assert layout["expiresDisplay"] == "none"
    assert layout["pageDisplay"] == "inline-flex"
    assert layout["commandbarPosition"] == "static"
    assert layout["header"]["height"] <= 125
    assert layout["commandbar"]["height"] <= 130
    assert layout["firstResult"]["y"] <= 330
    assert layout["scrollY"] > 0
    assert layout["commandbarOverlapsVisibleCard"] is False


def test_generate_result_page_writes_tokenized_safe_html(tmp_path) -> None:
    config = make_config(tmp_path)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    page = generate_result_page(
        query="5712g </script><script>alert(1)</script>",
        results=[
            SearchResult(
                listing_text="5712G </script><script>alert(1)</script>",
                seller="cookie=secret",
                posted_date="April 9, 2026",
                image_url="/images/5712g.jpg",
                source_url="/listing/5712g",
                raw_listing_text="raw cookie=secret data <html>full source</html>",
                seller_phone="17826241887",
                similar_results=(SearchResult("similar token=secret"),),
            ),
            SearchResult("second result should be bounded out"),
        ],
        offset=0,
        limit=5,
        total_count=2,
        next_offset=None,
        config=config,
        now=now,
    )

    assert page is not None
    assert page.url.startswith("https://mcp.example/results/")
    assert page.expires_at == "2026-06-07T12:01:00Z"
    assert page.result_count == 1
    assert page.to_payload()["schema_version"] == 1

    files = list(config.storage_dir.glob("*.html"))
    assert len(files) == 1
    assert files[0].stem == page.url.rsplit("/", maxsplit=1)[1]
    sidecar_files = list(config.storage_dir.glob("*.json"))
    assert len(sidecar_files) == 1
    assert sidecar_files[0].stem == files[0].stem
    html = files[0].read_text(encoding="utf-8")
    sidecar = json.loads(sidecar_files[0].read_text(encoding="utf-8"))

    assert "let results = null;" in html
    assert "5712G \\u003c/script\\u003e\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in html
    assert "https://watchfacts.example/images/5712g.jpg" in html
    assert "https://watchfacts.example/listing/5712g" in html
    assert "raw_listing_text" not in html
    assert "raw cookie=secret" not in html
    assert "cookie=secret" not in html
    assert "token=secret" not in html
    assert "second result should be bounded out" not in html
    assert isinstance(sidecar["action_nonce"], str)
    assert len(sidecar["action_nonce"]) >= 16
    assert sidecar["payload"]["actions"] == {
        "action_nonce": sidecar["action_nonce"],
        "openwa_draft_url": f"{page.url}/actions/openwa-draft",
        "report_url": f"{page.url}/actions/report",
    }
    assert sidecar["payload"]["query"] == "5712g </script><script>alert(1)</script>"
    assert sidecar["payload"]["result_page_schema_version"] == 1
    assert (
        sidecar["payload"]["results"][0]["source_url"]
        == "https://watchfacts.example/listing/5712g"
    )
    assert sidecar["payload"]["results"][0]["result_id"].startswith("watchfacts:")
    assert sidecar["payload"]["results"][0]["stable_listing_id"].startswith(
        "watchfacts-listing:"
    )
    assert sidecar["payload"]["results"][0]["source_result_id"] == (
        sidecar["payload"]["results"][0]["result_id"]
    )
    assert "raw_listing_text" not in json.dumps(sidecar, ensure_ascii=False)
    assert "raw cookie=secret" not in json.dumps(sidecar, ensure_ascii=False)
    assert "cookie=secret" not in json.dumps(sidecar, ensure_ascii=False)
    assert "token=secret" not in json.dumps(sidecar, ensure_ascii=False)


def test_read_result_page_action_payload_reports_sidecar_states(tmp_path) -> None:
    config = make_config(tmp_path)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    page = generate_result_page(
        query="5712g",
        results=[SearchResult("5712G", source_url="/listing/5712g")],
        now=now,
        config=config,
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]

    found = read_result_page_action_payload(
        token,
        config=config,
        now=now + timedelta(seconds=30),
    )
    assert found.status_code == 200
    assert found.error is None
    assert found.action_nonce
    assert found.payload is not None
    assert found.payload["query"] == "5712g"
    assert found.payload["actions"] == {
        "action_nonce": found.action_nonce,
        "openwa_draft_url": f"{page.url}/actions/openwa-draft",
        "report_url": f"{page.url}/actions/report",
    }
    assert found.payload["results"][0]["result_id"].startswith("watchfacts:")
    assert found.payload["results"][0]["stable_listing_id"].startswith(
        "watchfacts-listing:"
    )
    assert (
        found.payload["results"][0]["source_url"]
        == "https://watchfacts.example/listing/5712g"
    )

    invalid = read_result_page_action_payload("../bad", config=config, now=now)
    assert invalid.status_code == 404
    assert invalid.error == "invalid_token"

    (config.storage_dir / f"{token}.json").unlink()
    missing = read_result_page_action_payload(token, config=config, now=now)
    assert missing.status_code == 404
    assert missing.error == "missing_sidecar"


def test_expired_result_page_cleanup_removes_sidecar(tmp_path) -> None:
    config = make_config(tmp_path)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    page = generate_result_page(
        query="5712g",
        results=[SearchResult("5712G")],
        now=now,
        config=config,
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]

    expired = read_result_page_action_payload(
        token,
        config=config,
        now=now + timedelta(seconds=61),
    )
    assert expired.status_code == 410
    assert expired.error == "expired"
    assert not (config.storage_dir / f"{token}.html").exists()
    assert not (config.storage_dir / f"{token}.json").exists()


def test_generate_result_page_returns_none_when_disabled(tmp_path) -> None:
    config = make_config(tmp_path, public_base_url="")

    page = generate_result_page(
        query="5712g",
        results=[SearchResult("5712G")],
        now=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        config=config,
    )

    assert page is None
    assert not config.storage_dir.exists()


def test_read_result_page_html_reports_missing_and_expired_tokens(tmp_path) -> None:
    config = make_config(tmp_path)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    page = generate_result_page(
        query="5712g",
        results=[SearchResult("5712G")],
        now=now,
        config=config,
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]

    found = read_result_page_html(token, config=config, now=now + timedelta(seconds=30))
    assert found.status_code == 200
    assert found.html is not None

    missing = read_result_page_html("missing-token", config=config, now=now)
    assert missing.status_code == 404
    assert missing.html is None

    expired = read_result_page_html(token, config=config, now=now + timedelta(seconds=61))
    assert expired.status_code == 410
    assert expired.html is None
    assert not (config.storage_dir / f"{token}.html").exists()
