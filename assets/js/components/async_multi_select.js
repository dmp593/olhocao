(() => {
  const debounceDelayMs = 250;

  document.querySelectorAll("[data-async-multi-select]").forEach((picker) => {
    const searchInput = picker.querySelector("[data-async-search]");
    const resultsPanel = picker.querySelector("[data-async-results-panel]");
    const resultsList = picker.querySelector("[data-async-results]");
    const resultsEmpty = picker.querySelector("[data-async-results-empty]");
    const selectedList = picker.querySelector("[data-async-selected-list]");
    const selectedEmpty = picker.querySelector("[data-async-selected-empty]");
    const loadMoreButton = picker.querySelector("[data-async-load-more]");
    const status = picker.querySelector("[data-async-status]");
    const resultTemplate = picker.querySelector("[data-async-result-template]");
    const selectedTemplate = picker.querySelector("[data-async-selected-template]");
    let nextPage = null;
    let debounceTimer = null;
    let requestController = null;

    const selectedIds = () =>
      new Set(
        [...selectedList.querySelectorAll("[data-async-selected-item]")].map(
          (item) => item.dataset.optionId,
        ),
      );

    const updateSelectedEmptyState = () => {
      selectedEmpty.classList.toggle(
        "hidden",
        selectedList.querySelector("[data-async-selected-item]") !== null,
      );
    };

    const addSelectedItem = (option) => {
      if (selectedIds().has(String(option.id))) return;
      const fragment = selectedTemplate.content.cloneNode(true);
      const item = fragment.querySelector("[data-async-selected-item]");
      item.dataset.optionId = option.id;
      item.querySelector("[data-option-label]").textContent = option.label;
      const description = item.querySelector("[data-option-description]");
      description.textContent = option.description || "";
      description.hidden = !option.description;
      const input = item.querySelector("input");
      input.name = picker.dataset.fieldName;
      input.value = option.id;
      item.querySelector("[data-async-remove]").setAttribute(
        "aria-label",
        `${item.querySelector("[data-async-remove] .sr-only").textContent}: ${option.label}`,
      );
      selectedList.append(fragment);
      updateSelectedEmptyState();
      renderResults(lastResults);
    };

    const renderResults = (options, append = false) => {
      if (!append) resultsList.replaceChildren();
      const chosen = selectedIds();
      options.forEach((option) => {
        if (chosen.has(String(option.id))) return;
        const fragment = resultTemplate.content.cloneNode(true);
        const button = fragment.querySelector("[data-async-result]");
        button.dataset.optionId = option.id;
        button.querySelector("[data-option-label]").textContent = option.label;
        const description = button.querySelector("[data-option-description]");
        description.textContent = option.description || "";
        description.hidden = !option.description;
        button.addEventListener("click", () => addSelectedItem(option));
        resultsList.append(fragment);
      });
      const hasResults = resultsList.childElementCount > 0;
      resultsEmpty.classList.toggle("hidden", hasResults);
      resultsPanel.classList.remove("hidden");
    };

    let lastResults = [];
    const fetchResults = async (page = 1, append = false) => {
      requestController?.abort();
      requestController = new AbortController();
      status.textContent = "";
      const url = new URL(picker.dataset.searchUrl, window.location.origin);
      url.searchParams.set("q", searchInput.value.trim());
      url.searchParams.set("page", page);
      try {
        const response = await fetch(url, {
          headers: { Accept: "application/json" },
          signal: requestController.signal,
        });
        if (!response.ok) throw new Error(`Search returned ${response.status}`);
        const payload = await response.json();
        lastResults = append ? [...lastResults, ...payload.results] : payload.results;
        renderResults(payload.results, append);
        nextPage = payload.next_page;
        loadMoreButton.hidden = nextPage === null;
      } catch (error) {
        if (error.name === "AbortError") return;
        status.textContent = picker.dataset.searchError;
        resultsPanel.classList.remove("hidden");
      }
    };

    picker.addEventListener("click", (event) => {
      const removeButton = event.target.closest("[data-async-remove]");
      if (!removeButton) return;
      removeButton.closest("[data-async-selected-item]").remove();
      updateSelectedEmptyState();
      renderResults(lastResults);
      searchInput.focus();
    });
    searchInput.addEventListener("focus", () => {
      if (resultsPanel.classList.contains("hidden")) fetchResults();
    });
    searchInput.addEventListener("input", () => {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(() => fetchResults(), debounceDelayMs);
    });
    loadMoreButton.addEventListener("click", () => {
      if (nextPage !== null) fetchResults(nextPage, true);
    });
    document.addEventListener("click", (event) => {
      if (!picker.contains(event.target)) resultsPanel.classList.add("hidden");
    });
    updateSelectedEmptyState();
  });
})();
