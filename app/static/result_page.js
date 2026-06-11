    let results = null;
    results = __WATCHFACTS_RESULTS_PAYLOAD__;

    const state = {
      filter: "",
      sort: "rank",
      density: "comfortable"
    };

    const els = {
      query: document.getElementById("queryText"),
      createdAt: document.getElementById("createdAt"),
      expiresAt: document.getElementById("expiresAt"),
      pageRange: document.getElementById("pageRange"),
      resultCount: document.getElementById("resultCount"),
      pageStatus: document.getElementById("pageStatus"),
      filter: document.getElementById("filterInput"),
      sort: document.getElementById("sortSelect"),
      list: document.getElementById("resultsList"),
      status: document.getElementById("statusText"),
      viewNote: document.getElementById("viewNote"),
      toast: document.getElementById("toast"),
      densityComfortable: document.getElementById("densityComfortable"),
      densityDense: document.getElementById("densityDense"),
      copyPageLink: document.getElementById("copyPageLink"),
      exportJson: document.getElementById("exportJson"),
      exportCsv: document.getElementById("exportCsv"),
      printPage: document.getElementById("printPage"),
      modal: document.getElementById("resultModal"),
      modalBackdrop: document.getElementById("resultModalBackdrop"),
      modalPanel: document.getElementById("resultModalPanel"),
      modalKicker: document.getElementById("resultModalKicker"),
      modalTitle: document.getElementById("resultModalTitle"),
      modalBody: document.getElementById("resultModalBody"),
      modalClose: document.getElementById("resultModalClose")
    };

    let lastModalTrigger = null;

    function text(value, fallback = "") {
      if (value === null || value === undefined || value === "") return fallback;
      return String(value);
    }

    function numberValue(value, fallback = 0) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    function truncate(value, maxLength) {
      const raw = text(value).replace(/\s+/g, " ").trim();
      if (raw.length <= maxLength) return raw;
      return raw.slice(0, maxLength - 3).trimEnd() + "...";
    }

    function allResults() {
      return results && Array.isArray(results.results) ? results.results : [];
    }

    function resultLabel(count) {
      const numeric = numberValue(count);
      return numeric === 1 ? "1 result" : String(numeric) + " results";
    }

    function formatDate(value) {
      const raw = text(value, "unknown");
      const parsed = Date.parse(raw);
      if (Number.isNaN(parsed)) return raw;
      return new Date(parsed).toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit"
      });
    }

    function pageRangeText() {
      if (!results) return "Unavailable";
      const offset = Math.max(numberValue(results.offset, 0), 0);
      const count = allResults().length;
      const total = numberValue(results.total_count, count);
      if (!count) return "0 of " + total;
      return String(offset + 1) + "-" + String(offset + count) + " of " + total;
    }

    function statusText() {
      if (!results) return "Unavailable";
      if (results.next_offset !== null && results.next_offset !== undefined) return "More available";
      const count = allResults().length;
      const total = numberValue(results.total_count, count);
      return count < total ? "Partial page" : "Complete";
    }

    function showToast(message) {
      els.toast.textContent = message;
      els.toast.classList.add("show");
      window.setTimeout(() => els.toast.classList.remove("show"), 1600);
    }

    async function copyText(value, label) {
      const content = text(value);
      if (!content) return;
      try {
        await navigator.clipboard.writeText(content);
      } catch (error) {
        const field = document.createElement("textarea");
        field.value = content;
        field.setAttribute("readonly", "");
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.appendChild(field);
        field.select();
        document.execCommand("copy");
        field.remove();
      }
      showToast(label + " copied");
    }

    function download(filename, mimeType, content) {
      const blob = new Blob([content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function csvEscape(value) {
      const raw = text(value);
      return '"' + raw.replaceAll('"', '""') + '"';
    }

    function exportCsv() {
      const rows = [["rank", "result_id", "listing_text", "seller", "posted_date", "seller_phone", "source_url"]];
      for (const item of currentResults()) {
        rows.push([
          item.rank,
          item.result_id,
          item.listing_text,
          item.seller,
          item.posted_date,
          item.seller_phone,
          item.source_url
        ]);
      }
      download("watchfacts-results.csv", "text/csv", rows.map(row => row.map(csvEscape).join(",")).join("\n"));
    }

    function exportJson() {
      download("watchfacts-results.json", "application/json", JSON.stringify(results || { results: [] }, null, 2));
    }

    function resultText(item) {
      const similarText = Array.isArray(item.similar_results)
        ? item.similar_results.map(similar => text(similar.listing_text)).join(" ")
        : "";
      return [
        item.rank,
        item.result_id,
        item.listing_text,
        item.seller,
        item.posted_date,
        item.seller_phone,
        item.source_url,
        similarText
      ].map(value => text(value).toLowerCase()).join(" ");
    }

    function postedTime(value) {
      const normalized = text(value).split("·")[0].split(" - ")[0].trim();
      const parsed = Date.parse(normalized);
      return Number.isNaN(parsed) ? 0 : parsed;
    }

    function currentResults() {
      const filter = state.filter.trim().toLowerCase();
      let items = allResults().filter(item => !filter || resultText(item).includes(filter));
      if (state.sort === "posted_desc") {
        items = items.slice().sort((a, b) => postedTime(b.posted_date) - postedTime(a.posted_date) || numberValue(a.rank) - numberValue(b.rank));
      } else if (state.sort === "seller") {
        items = items.slice().sort((a, b) => text(a.seller).localeCompare(text(b.seller)) || numberValue(a.rank) - numberValue(b.rank));
      } else {
        items = items.slice().sort((a, b) => numberValue(a.rank) - numberValue(b.rank));
      }
      return items;
    }

    function createNode(tag, className, value) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (value !== undefined) node.textContent = value;
      return node;
    }

    function makeButton(label, title, onClick, className = "action-button") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = label;
      button.title = title;
      button.setAttribute("aria-label", title);
      button.addEventListener("click", onClick);
      return button;
    }

    function focusWithoutScroll(node) {
      if (!node || typeof node.focus !== "function") return;
      try {
        node.focus({ preventScroll: true });
      } catch (error) {
        node.focus();
      }
    }

    function modalScrollbarCompensation() {
      const width = window.innerWidth - document.documentElement.clientWidth;
      return width > 0 ? width : 0;
    }

    function needsModalScrollbarCompensation() {
      return !(window.CSS && CSS.supports && CSS.supports("scrollbar-gutter", "stable"));
    }

    function lockModalScroll() {
      document.documentElement.style.setProperty(
        "--modal-scrollbar-compensation",
        String(needsModalScrollbarCompensation() ? modalScrollbarCompensation() : 0) + "px"
      );
      document.body.classList.add("modal-open");
    }

    function unlockModalScroll() {
      document.body.classList.remove("modal-open");
      document.documentElement.style.removeProperty("--modal-scrollbar-compensation");
    }

    function hostName(value) {
      const raw = text(value);
      if (!raw) return "";
      try {
        return new URL(raw).hostname.replace(/^www\\./, "");
      } catch (error) {
        return raw;
      }
    }

    function listingDisplayText(item) {
      if (!item) return "";
      const density = state.density === "dense" ? "dense" : "comfortable";
      const preview = item && item.listing_text_preview;
      if (preview && typeof preview === "object") {
        const previewValue = text(preview[density] || preview.comfortable);
        if (previewValue) return previewValue;
      }
      if (density === "dense") {
        return truncate(item.listing_text, 150);
      }
      return truncate(item.listing_text, 220);
    }

    function copyField(value, fallback = "-") {
      const normalized = text(value).replace(/\s+/g, " ").trim();
      return normalized || fallback;
    }

    function padDatePart(value) {
      return String(value).padStart(2, "0");
    }

    function formatCopyDate(value) {
      const raw = copyField(value);
      if (raw === "-") return raw;
      const normalized = raw.split("·", 1)[0].split(" - ", 1)[0].trim();
      const iso = normalized.match(/^(\d{4})-(\d{2})-(\d{2})/);
      if (iso) return iso[3] + "/" + iso[2] + "/" + iso[1];

      const monthMatch = normalized.match(/^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$/);
      const monthNumber = monthMatch && {
        january: 1, jan: 1,
        february: 2, feb: 2,
        march: 3, mar: 3,
        april: 4, apr: 4,
        may: 5,
        june: 6, jun: 6,
        july: 7, jul: 7,
        august: 8, aug: 8,
        september: 9, sep: 9, sept: 9,
        october: 10, oct: 10,
        november: 11, nov: 11,
        december: 12, dec: 12
      }[monthMatch[1].toLowerCase()];
      if (monthNumber) {
        return padDatePart(monthMatch[2]) + "/" + padDatePart(monthNumber) + "/" + monthMatch[3];
      }
      return raw;
    }

    function compactListingItem(item) {
      const compactText = listingDisplayText(item);
      if (!compactText) return item;
      const currentText = copyField(item && item.listing_text, "No listing text");
      if (compactText === currentText) return item;
      return Object.assign({}, item || {}, { listing_text: compactText });
    }

    function formattedListingFields(item) {
      return [
        {
          label: "Listing",
          icon: "Listing: ",
          copyPrefix: "Listing: ",
          className: "listing-line-title",
          value: copyField(item.listing_text, "No listing text")
        },
        {
          label: "Seller",
          icon: "Seller: ",
          copyPrefix: "Seller: ",
          className: "listing-line-meta listing-line-seller",
          value: copyField(item.seller)
        },
        {
          label: "Posted",
          icon: "Date: ",
          copyPrefix: "Date: ",
          className: "listing-line-meta listing-line-date",
          value: formatCopyDate(item.posted_date)
        }
      ];
    }

    function formatListingCopy(item) {
      return formattedListingFields(item).map((field) => field.copyPrefix + field.value).join("\n");
    }

    function createFormattedListingDisplay(item, className = "", titleTag = "h2") {
      const compactClass = className && (className.includes("listing-display-card") || className.includes("listing-display-similar"));
      const sourceItem = compactClass ? compactListingItem(item) : item;
      const displayClass = ["listing-display", className].filter(Boolean).join(" ");
      const display = createNode("div", displayClass);
      for (const field of formattedListingFields(sourceItem)) {
        const tagName = field.className.includes("listing-line-title") ? titleTag : "p";
        const row = document.createElement(tagName);
        row.className = "listing-line " + field.className + (tagName === "h2" ? " listing-title" : "");
        row.title = field.label + ": " + field.value;
        row.setAttribute("aria-label", row.title);
        const icon = createNode("span", "listing-icon", field.icon);
        icon.setAttribute("aria-hidden", "true");
        row.append(icon, createNode("span", "listing-value", field.value));
        display.appendChild(row);
      }
      return display;
    }

    function makeFormattedCopyButton(item) {
      const button = makeButton("", "Copy formatted listing text", () => copyText(formatListingCopy(item), "Listing text"));
      const label = createNode("span", "copy-label", "Copy Text");
      label.dataset.full = "Copy Text";
      label.dataset.short = "Copy";
      label.setAttribute("aria-hidden", "true");
      button.appendChild(label);
      return button;
    }

    function hasImage(item) {
      return Boolean(text(item && item.image_url).trim());
    }

    function similarCount(item) {
      return Array.isArray(item && item.similar_results) ? item.similar_results.length : 0;
    }

    function detailMeta(item) {
      const values = [];
      if (item.seller_phone) values.push({ label: "Phone: ", value: text(item.seller_phone) });
      const source = hostName(item.source_url);
      if (source) values.push({ label: "Source: ", value: source, copyValue: text(item.source_url), copyLabel: "Source URL" });
      return values;
    }

    function createDetailsMeta(item) {
      const meta = createNode("div", "result-details-meta");
      for (const value of detailMeta(item)) {
        const chip = createNode("div", "detail-chip");
        chip.setAttribute("aria-label", value.label + value.value);
        const label = createNode("span", "detail-label");
        label.appendChild(createNode("span", "", value.label));
        const content = createNode("span", "detail-value", value.value);
        content.title = value.value;
        chip.append(label, content);
        if (value.copyValue) {
          chip.appendChild(makeButton("Copy", "Copy " + value.copyLabel, () => copyText(value.copyValue, value.copyLabel), "mini-copy"));
        }
        meta.appendChild(chip);
      }
      return meta;
    }

    function createModalFact(label, value) {
      const fact = createNode("span", "modal-fact");
      fact.setAttribute("aria-label", label + ": " + value);
      fact.append(createNode("span", "modal-fact-label", label + ": "), createNode("strong", "", value));
      return fact;
    }

    function createModalQuickFacts(item) {
      const facts = createNode("div", "modal-quick-facts");
      facts.appendChild(createModalFact("Seller", copyField(item.seller)));
      facts.appendChild(createModalFact("Posted", formatCopyDate(item.posted_date)));
      if (similarCount(item)) facts.appendChild(createModalFact("Similar", String(similarCount(item))));
      facts.appendChild(createModalFact("Media", hasImage(item) ? "Image" : "No image"));
      return facts;
    }

    function createThumb(item) {
      const thumb = createNode("div", "thumb");
      if (hasImage(item)) {
        const img = document.createElement("img");
        img.src = item.image_url;
        img.alt = "Listing image for result " + text(item.rank, "");
        img.loading = "lazy";
        img.addEventListener("error", () => {
          img.remove();
          thumb.textContent = "No image";
        });
        thumb.appendChild(img);
      } else {
        thumb.textContent = "No image";
      }
      return thumb;
    }

    function populateSimilarPanel(item, panel) {
      if (panel.dataset.loaded === "true") return;
      panel.replaceChildren();
      const similarTitle = createNode("h3", "similar-title", "Similar listings");
      panel.appendChild(similarTitle);
      for (const similarItem of item.similar_results) {
        const row = createNode("div", "similar-item");
        row.appendChild(createFormattedListingDisplay(similarItem, "listing-display-similar", "div"));
        const rowMeta = createNode("div", "similar-meta");
        if (similarItem.seller_phone) rowMeta.appendChild(createNode("span", "", "Phone: " + similarItem.seller_phone));
        if (similarItem.source_url) rowMeta.appendChild(createNode("span", "", "Source: " + hostName(similarItem.source_url)));
        if (rowMeta.childElementCount) row.appendChild(rowMeta);
        panel.appendChild(row);
      }
      panel.dataset.loaded = "true";
    }

    function resultActionConfig() {
      const actions = results && results.actions;
      return actions && typeof actions === "object" ? actions : {};
    }

    async function postResultAction(url, item, extra = {}) {
      const actions = resultActionConfig();
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({
          action_nonce: text(actions.action_nonce),
          result_id: text(item.result_id)
        }, extra))
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      if (!response.ok || !payload || payload.ok === false) {
        const message = payload && payload.message ? payload.message : "Action failed.";
        throw new Error(message);
      }
      return payload;
    }

    function createActionStatus() {
      const status = createNode("div", "modal-action-status");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      return status;
    }

    function setActionStatus(status, message, className = "") {
      status.className = "modal-action-status" + (className ? " " + className : "");
      status.textContent = message;
    }

    function createOpenWaDraftAction(item) {
      const actions = resultActionConfig();
      const container = createNode("div", "modal-primary-action");
      container.appendChild(createNode("div", "action-card-title", "OpenWA handoff"));
      container.appendChild(createNode("p", "action-card-description", "Create a seller chat draft from this exact result_id."));
      const status = createActionStatus();
      if (!actions.openwa_draft_url || !actions.action_nonce) {
        container.appendChild(makeButton("Copy OpenWA", "Copy prompt to create an OpenWA chat draft", () => copyText(openWaPrompt(item), "OpenWA prompt")));
        container.appendChild(status);
        setActionStatus(status, "Draft creation is not available on this page.");
        return container;
      }

      const button = makeButton("Create OpenWA draft", "Create an OpenWA chat draft", async () => {
        button.disabled = true;
        button.textContent = "Creating...";
        setActionStatus(status, "Creating draft...");
        try {
          const payload = await postResultAction(actions.openwa_draft_url, item);
          button.textContent = "Draft created";
          setActionStatus(status, "Draft created.", "success");
          if (payload.dashboard_url) {
            const link = document.createElement("a");
            link.className = "draft-link";
            link.href = payload.dashboard_url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "Open draft";
            status.append(" ");
            status.appendChild(link);
          }
        } catch (error) {
          button.disabled = false;
          button.textContent = "Create OpenWA draft";
          setActionStatus(status, error.message || "OpenWA draft failed.", "error");
        }
      });
      container.append(button, status);
      return container;
    }

    function createReportIssueForm(item) {
      const actions = resultActionConfig();
      const form = createNode("form", "report-form");
      form.appendChild(createNode("div", "action-card-title", "Quality feedback"));
      form.appendChild(createNode("p", "action-card-description", "Report missing details or a wrong match for review."));
      const status = createActionStatus();
      if (!actions.report_url || !actions.action_nonce) {
        form.appendChild(makeButton("Copy Report", "Copy prompt to report this result", () => copyText(reportPrompt(item), "Report prompt")));
        form.appendChild(status);
        setActionStatus(status, "Issue reporting is not available on this page.");
        return form;
      }

      const reasonLabel = createNode("label", "", "Issue reason");
      const reason = document.createElement("select");
      reason.name = "reason";
      reason.required = true;
      for (const option of [
        ["wrong_result", "Wrong result"],
        ["missing_info", "Missing info"],
        ["other", "Other"]
      ]) {
        const node = document.createElement("option");
        node.value = option[0];
        node.textContent = option[1];
        reason.appendChild(node);
      }
      reasonLabel.appendChild(reason);

      const notesLabel = createNode("label", "", "Notes");
      const notes = document.createElement("textarea");
      notes.name = "notes";
      notes.maxLength = 1000;
      notes.placeholder = "Optional context for review";
      notesLabel.appendChild(notes);

      const submit = makeButton("Submit report", "Submit result issue report", () => {}, "action-button");
      submit.type = "submit";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        submit.disabled = true;
        reason.disabled = true;
        notes.disabled = true;
        submit.textContent = "Submitting...";
        setActionStatus(status, "Submitting report...");
        try {
          const payload = await postResultAction(actions.report_url, item, {
            reason: reason.value,
            notes: notes.value
          });
          submit.textContent = payload.issue_ref ? "Reported as " + payload.issue_ref : "Reported";
          setActionStatus(status, "Issue recorded for review.", "success");
        } catch (error) {
          reason.disabled = false;
          notes.disabled = false;
          submit.disabled = false;
          submit.textContent = "Submit report";
          setActionStatus(status, error.message || "Report failed.", "error");
        }
      });

      form.append(reasonLabel, notesLabel, submit, status);
      return form;
    }

    function createOverflowActions(item, similarPanel, className = "") {
      const secondaryClass = ["result-actions-secondary", className].filter(Boolean).join(" ");
      const secondary = createNode("div", secondaryClass);
      secondary.appendChild(createOpenWaDraftAction(item));
      secondary.appendChild(createReportIssueForm(item));
      const utilities = createNode("div", "modal-utility-actions");
      const count = similarCount(item);
      if (count) {
        const similarButton = makeButton("+" + String(count) + " similar", "Show similar listings", () => {
          const shouldShow = similarPanel.hidden;
          if (shouldShow) populateSimilarPanel(item, similarPanel);
          similarPanel.hidden = !shouldShow;
          similarButton.setAttribute("aria-expanded", String(shouldShow));
          similarButton.textContent = shouldShow ? "Hide similar" : "+" + String(count) + " similar";
          similarButton.setAttribute("aria-label", shouldShow ? "Hide similar listings" : "Show similar listings");
          similarButton.title = shouldShow ? "Hide similar listings" : "Show similar listings";
        });
        similarButton.setAttribute("aria-expanded", "false");
        similarButton.setAttribute("aria-controls", similarPanel.id);
        utilities.appendChild(similarButton);
      }
      if (utilities.childElementCount) secondary.appendChild(utilities);
      return secondary;
    }

    function modalKicker(item) {
      return "WatchFacts listing";
    }

    function modalTitle(item) {
      return "Result #" + text(item.rank, "unknown") + " details";
    }

    function createResultDetailsContent(item) {
      const details = createNode("section", "result-details");
      const listingSection = createNode("section", "modal-section modal-listing-section");
      const hero = createNode("div", "modal-hero");
      const mediaCard = createNode("div", "modal-media-card");
      if (!hasImage(item)) mediaCard.classList.add("modal-media-card-no-image");
      mediaCard.append(createThumb(item), createModalQuickFacts(item));
      const listingCopy = createNode("div", "modal-listing-copy");
      listingCopy.append(
        createNode("div", "modal-section-label", "Listing snapshot"),
        createFormattedListingDisplay(item, "listing-display-detail", "div")
      );
      hero.append(mediaCard, listingCopy);
      listingSection.appendChild(hero);
      details.appendChild(listingSection);

      const similar = createNode("section", "similar-panel");
      similar.hidden = true;
      similar.id = "modal-similar-" + text(item.rank, "result").replace(/[^a-zA-Z0-9_-]/g, "-");
      const actionsSection = createNode("section", "modal-actions-section");
      actionsSection.appendChild(createOverflowActions(item, similar, "modal-actions"));
      details.appendChild(actionsSection);

      const metaSection = createNode("section", "modal-section modal-meta-section");
      const idWrap = createNode("div", "result-id-wrap");
      const idIcon = createNode("span", "result-id-icon", "ID: ");
      idIcon.setAttribute("aria-hidden", "true");
      const idText = createNode("span", "result-id", text(item.result_id, "No result_id"));
      const idCopy = makeButton("Copy ID", "Copy result_id", () => copyText(item.result_id, "Result ID"), "mini-copy");
      idWrap.setAttribute("aria-label", "ID: " + text(item.result_id, "No result_id"));
      idWrap.append(idIcon, idText, idCopy);
      metaSection.appendChild(idWrap);

      const detailsMeta = createDetailsMeta(item);
      if (detailsMeta.childElementCount) metaSection.appendChild(detailsMeta);
      details.appendChild(metaSection);

      if (similarCount(item)) {
        const similarSection = createNode("section", "modal-section modal-similar-section");
        similarSection.appendChild(similar);
        details.appendChild(similarSection);
      }
      return details;
    }

    function modalFocusableNodes() {
      return Array.from(els.modalPanel.querySelectorAll(
        "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
      ));
    }

    function openDetailsModal(item, trigger) {
      lastModalTrigger = trigger || null;
      els.modalKicker.textContent = modalKicker(item);
      els.modalTitle.textContent = modalTitle(item);
      els.modalBody.replaceChildren(createResultDetailsContent(item));
      els.modal.hidden = false;
      lockModalScroll();
      focusWithoutScroll(els.modalClose);
    }

    function closeDetailsModal(options = {}) {
      const restoreFocus = options.restoreFocus !== false;
      if (els.modal.hidden) return;
      els.modal.hidden = true;
      els.modalKicker.textContent = "";
      els.modalTitle.textContent = "Result details";
      els.modalBody.replaceChildren();
      unlockModalScroll();
      if (restoreFocus && lastModalTrigger) focusWithoutScroll(lastModalTrigger);
      lastModalTrigger = null;
    }

    function handleModalKeydown(event) {
      if (els.modal.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeDetailsModal();
        return;
      }
      if (event.key !== "Tab") return;

      const nodes = modalFocusableNodes();
      if (!nodes.length) {
        event.preventDefault();
        focusWithoutScroll(els.modalClose);
        return;
      }

      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function createResultCard(item) {
      const imagePresent = hasImage(item);
      const article = document.createElement("article");
      article.className = "result-card " + (imagePresent ? "has-image" : "no-image");

      const media = createNode("div", "result-media");
      media.appendChild(createThumb(item));
      media.appendChild(createNode("span", "rank-badge", "#" + text(item.rank, "-")));

      const lead = createNode("div", "result-lead");
      const listingDisplay = createFormattedListingDisplay(item, "listing-display-card");
      lead.appendChild(listingDisplay);
      const body = createNode("div", "result-body");

      const actions = createNode("div", "result-actions");
      const primary = createNode("div", "result-actions-primary");
      primary.appendChild(makeFormattedCopyButton(item));
      if (item.source_url) {
        const link = document.createElement("a");
        link.className = "source-link";
        link.href = item.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Source";
        link.title = "Open source listing";
        link.setAttribute("aria-label", "Open source listing");
        primary.appendChild(link);
      } else {
        const sourceButton = makeButton("Source", "No source URL", () => {});
        sourceButton.disabled = true;
        primary.appendChild(sourceButton);
      }

      const detailsToggle = makeButton("More", "Show result details", () => {
        openDetailsModal(item, detailsToggle);
      }, "details-toggle");
      detailsToggle.setAttribute("aria-haspopup", "dialog");
      detailsToggle.setAttribute("aria-controls", "resultModal");
      actions.append(primary, detailsToggle);
      body.append(lead, actions);

      article.append(media, body);
      return article;
    }

    function openWaPrompt(item) {
      return [
        "Create an OpenWA chat draft for this WatchFacts result.",
        "query: " + text(results && results.query),
        "result_id: " + text(item.result_id)
      ].join("\n");
    }

    function reportPrompt(item) {
      return [
        "Report an issue for this WatchFacts result.",
        "query: " + text(results && results.query),
        "result_id: " + text(item.result_id),
        "reason: wrong_result | missing_info | other",
        "notes: "
      ].join("\n");
    }

    function emptyState(title, message) {
      const node = createNode("div", "empty");
      const heading = createNode("h2", "empty-title", title);
      const body = createNode("p", "empty-message", message);
      node.append(heading, body);
      return node;
    }

    function renderHeader() {
      if (!results) {
        els.query.textContent = "No result payload";
        els.createdAt.textContent = "Unknown";
        els.expiresAt.textContent = "Unknown";
        els.pageRange.textContent = "Unavailable";
        els.resultCount.textContent = "0";
        els.pageStatus.textContent = "Unavailable";
        return;
      }

      const total = numberValue(results.total_count, allResults().length);
      els.query.textContent = text(results.query, "Query unavailable");
      els.createdAt.textContent = formatDate(results.created_at);
      els.expiresAt.textContent = formatDate(results.expires_at);
      els.pageRange.textContent = pageRangeText();
      els.resultCount.textContent = String(total);
      els.pageStatus.textContent = statusText();
    }

    function renderToolbarState() {
      const hasPayload = Boolean(results);
      els.filter.disabled = !hasPayload;
      els.sort.disabled = !hasPayload;
      els.copyPageLink.disabled = !hasPayload;
      els.exportJson.disabled = !hasPayload;
      els.exportCsv.disabled = !hasPayload;
      els.printPage.disabled = !hasPayload;
      els.densityComfortable.disabled = !hasPayload;
      els.densityDense.disabled = !hasPayload;
      els.densityComfortable.setAttribute("aria-pressed", String(state.density === "comfortable"));
      els.densityDense.setAttribute("aria-pressed", String(state.density === "dense"));
      els.densityComfortable.classList.toggle("is-active", state.density === "comfortable");
      els.densityDense.classList.toggle("is-active", state.density === "dense");
      els.list.classList.toggle("density-dense", state.density === "dense");
      els.list.classList.toggle("density-comfortable", state.density === "comfortable");
      els.viewNote.textContent = hasPayload ? (state.density === "dense" ? "Dense view" : "Comfortable view") : "";
    }

    function renderResults() {
      closeDetailsModal({ restoreFocus: false });
      if (!results) {
        els.status.textContent = "No result payload loaded.";
        els.list.replaceChildren(emptyState("No result payload", "This page was opened without an injected WatchFacts result payload."));
        return;
      }

      const sourceItems = allResults();
      const items = currentResults();
      if (!sourceItems.length) {
        els.status.textContent = "0 shown";
        els.list.replaceChildren(emptyState("No results", "This query returned no WatchFacts listings."));
        return;
      }

      if (!items.length) {
        els.status.textContent = "0 of " + sourceItems.length + " matching";
        els.list.replaceChildren(emptyState("No matching results", "The current filter does not match any listing on this page."));
        return;
      }

      els.status.textContent = state.filter
        ? String(items.length) + " of " + String(sourceItems.length) + " matching"
        : resultLabel(items.length) + " shown";
      els.list.replaceChildren(...items.map(createResultCard));
    }

    function render() {
      renderHeader();
      renderToolbarState();
      renderResults();
    }

    function initializeDensity() {
      state.density = "comfortable";
    }

    els.filter.addEventListener("input", event => {
      state.filter = event.target.value;
      render();
    });
    els.sort.addEventListener("change", event => {
      state.sort = event.target.value;
      render();
    });
    els.densityComfortable.addEventListener("click", () => {
      state.density = "comfortable";
      render();
    });
    els.densityDense.addEventListener("click", () => {
      state.density = "dense";
      render();
    });
    els.copyPageLink.addEventListener("click", () => copyText(window.location.href, "Page link"));
    els.exportJson.addEventListener("click", exportJson);
    els.exportCsv.addEventListener("click", exportCsv);
    els.printPage.addEventListener("click", () => window.print());
    els.modalClose.addEventListener("click", () => closeDetailsModal());
    els.modalBackdrop.addEventListener("click", () => closeDetailsModal());
    document.addEventListener("keydown", handleModalKeydown);

    initializeDensity();
    render();
