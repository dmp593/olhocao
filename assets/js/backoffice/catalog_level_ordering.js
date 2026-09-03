function catalogCsrfToken() {
  const cookie = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("csrftoken="));
  return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : "";
}

function initializeCatalogLevelOrdering(container) {
  const levelItems = () => [...container.querySelectorAll("[data-level-order-item]")];
  const levelIds = () => levelItems().map((item) => Number(item.dataset.levelId));
  const handles = () => [...container.querySelectorAll("[data-level-drag-handle]")];
  const status = document.querySelector("[data-level-order-status]");
  let draggedItem = null;
  let orderBeforeMove = [];
  let isSaving = false;

  function showStatus(message, style) {
    if (!status) return;
    status.textContent = message;
    status.classList.remove("hidden", "text-accent-700", "text-red-700", "text-primary-500");
    status.classList.add(style);
  }

  function restoreOrder(orderedIds) {
    const itemsById = new Map(levelItems().map((item) => [Number(item.dataset.levelId), item]));
    orderedIds.forEach((levelId) => container.append(itemsById.get(levelId)));
  }

  function setHandlesDisabled(disabled) {
    handles().forEach((handle) => {
      handle.disabled = disabled;
      handle.setAttribute("aria-disabled", String(disabled));
    });
  }

  async function saveOrder(previousOrder) {
    if (isSaving) return;
    isSaving = true;
    setHandlesDisabled(true);
    showStatus(container.dataset.savingMessage, "text-primary-500");

    try {
      const response = await fetch(container.dataset.reorderUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": catalogCsrfToken(),
        },
        body: JSON.stringify({ level_ids: levelIds() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || container.dataset.errorMessage);
      showStatus(payload.message || container.dataset.savedMessage, "text-accent-700");
    } catch (_error) {
      restoreOrder(previousOrder);
      showStatus(container.dataset.errorMessage, "text-red-700");
    } finally {
      isSaving = false;
      setHandlesDisabled(false);
    }
  }

  container.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("[data-level-drag-handle]");
    if (!handle || isSaving) {
      event.preventDefault();
      return;
    }

    draggedItem = handle.closest("[data-level-order-item]");
    orderBeforeMove = levelIds();
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggedItem.dataset.levelId);
    event.dataTransfer.setDragImage(draggedItem, draggedItem.offsetWidth / 2, 24);
    draggedItem.classList.add("ui-sortable-item-dragging");
  });

  container.addEventListener("dragover", (event) => {
    if (!draggedItem) return;
    const targetItem = event.target.closest("[data-level-order-item]");
    if (!targetItem || targetItem === draggedItem) return;

    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const targetBounds = targetItem.getBoundingClientRect();
    const placeAfterTarget = event.clientY > targetBounds.top + targetBounds.height / 2;
    targetItem[placeAfterTarget ? "after" : "before"](draggedItem);
  });

  container.addEventListener("drop", (event) => {
    if (draggedItem) event.preventDefault();
  });

  container.addEventListener("dragend", () => {
    if (!draggedItem) return;
    draggedItem.classList.remove("ui-sortable-item-dragging");
    draggedItem = null;

    if (orderBeforeMove.join(",") !== levelIds().join(",")) {
      saveOrder(orderBeforeMove);
    }
  });

  handles().forEach((handle) => {
    handle.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown"].includes(event.key) || isSaving) return;
      const item = handle.closest("[data-level-order-item]");
      const sibling = event.key === "ArrowUp"
        ? item.previousElementSibling
        : item.nextElementSibling;
      if (!sibling?.matches("[data-level-order-item]")) return;

      event.preventDefault();
      const previousOrder = levelIds();
      sibling[event.key === "ArrowUp" ? "before" : "after"](item);
      handle.focus();
      saveOrder(previousOrder);
    });
  });
}

document.querySelectorAll("[data-level-ordering]").forEach(initializeCatalogLevelOrdering);
