(() => {
  const picker = document.querySelector("[data-optional-service-picker]");
  if (!picker) return;

  const availableList = picker.querySelector("[data-available-service-list]");
  const selectedList = picker.querySelector("[data-selected-service-list]");
  const addButton = picker.querySelector("[data-add-optional-services]");
  const removeButton = picker.querySelector("[data-remove-optional-services]");
  const searchInput = picker.querySelector("[data-optional-service-search]");
  const status = picker.querySelector("[data-optional-service-status]");
  const initialIds = new Set(
    (picker.dataset.initialIds || "").split(",").filter(Boolean),
  );

  const directCards = (list) =>
    [...list.querySelectorAll("[data-optional-service-card]:not([data-inherited-service])")];

  const checkedCards = (list) =>
    directCards(list).filter(
      (card) => card.querySelector("[data-optional-service-choice]")?.checked,
    );

  const sortCards = (list) => {
    [...list.querySelectorAll("[data-optional-service-card]")]
      .sort((left, right) =>
        left.dataset.sortKey.localeCompare(right.dataset.sortKey, undefined, {
          numeric: true,
          sensitivity: "base",
        }),
      )
      .forEach((card) => list.append(card));
  };

  const selectedIds = () =>
    new Set(directCards(selectedList).map((card) => card.dataset.serviceId));

  const setsAreEqual = (left, right) =>
    left.size === right.size && [...left].every((value) => right.has(value));

  const updateSearch = () => {
    const query = searchInput.value.trim().toLocaleLowerCase();
    picker.querySelectorAll("[data-optional-service-card]").forEach((card) => {
      card.hidden = Boolean(query) && !card.dataset.searchText.includes(query);
    });
  };

  const updateInterface = () => {
    addButton.disabled = checkedCards(availableList).length === 0;
    removeButton.disabled = checkedCards(selectedList).length === 0;

    const availableCount = directCards(availableList).length;
    const selectedCount = selectedList.querySelectorAll(
      "[data-optional-service-card]",
    ).length;
    picker.querySelector("[data-available-service-count]").textContent = availableCount;
    picker.querySelector("[data-selected-service-count]").textContent = selectedCount;
    picker.querySelector("[data-available-service-empty]").classList.toggle(
      "hidden",
      availableCount > 0,
    );
    picker.querySelector("[data-selected-service-empty]").classList.toggle(
      "hidden",
      selectedCount > 0,
    );

    const hasChanges = !setsAreEqual(initialIds, selectedIds());
    status.textContent = hasChanges
      ? picker.dataset.dirtyMessage
      : picker.dataset.cleanMessage;
    status.classList.toggle("text-accent-800", hasChanges);
    status.classList.toggle("text-primary-600", !hasChanges);
    updateSearch();
  };

  const moveCheckedCards = (source, destination, associate) => {
    const cards = checkedCards(source);
    cards.forEach((card) => {
      const checkbox = card.querySelector("[data-optional-service-choice]");
      checkbox.checked = false;
      card.querySelector("[data-optional-service-association]")?.remove();
      if (associate) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "add_on_ids";
        input.value = card.dataset.serviceId;
        input.dataset.optionalServiceAssociation = "";
        card.append(input);
      }
      destination.append(card);
    });
    sortCards(destination);
    cards[0]?.querySelector("[data-optional-service-choice]")?.focus();
    updateInterface();
  };

  picker.addEventListener("change", (event) => {
    if (event.target.matches("[data-optional-service-choice]")) updateInterface();
  });
  addButton.addEventListener("click", () =>
    moveCheckedCards(availableList, selectedList, true),
  );
  removeButton.addEventListener("click", () =>
    moveCheckedCards(selectedList, availableList, false),
  );
  searchInput.addEventListener("input", updateSearch);

  updateInterface();
})();
