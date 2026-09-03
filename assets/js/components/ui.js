document.addEventListener("click", (event) => {
  const dismissButton = event.target.closest("[data-dismiss-parent]");
  if (dismissButton) {
    dismissButton.parentElement?.remove();
    return;
  }

  const openButton = event.target.closest("[data-dialog-open]");
  if (openButton) {
    const dialog = document.getElementById(openButton.dataset.dialogOpen);
    if (dialog instanceof HTMLDialogElement) dialog.showModal();
    return;
  }

  const closeButton = event.target.closest("[data-dialog-close]");
  if (closeButton) {
    closeButton.closest("dialog")?.close();
  }
});

function updateDisclosure(control) {
  const targetIds = (control.dataset.disclosureTarget || "").split(",");
  const expectedValues = (control.dataset.disclosureValues || "")
    .split(";")
    .filter(Boolean);

  targetIds.forEach((targetId, index) => {
    const target = document.getElementById(targetId.trim());
    if (!target) return;

    const isOpen = control.type === "checkbox"
      ? control.checked
      : control.value === expectedValues[index];
    target.hidden = !isOpen;
    target.setAttribute("aria-hidden", String(!isOpen));
  });

  if (targetIds.length === 1) {
    control.setAttribute("aria-expanded", String(!document.getElementById(targetIds[0])?.hidden));
  }
}

function initializeDisclosures() {
  document.querySelectorAll("[data-disclosure-target]").forEach(updateDisclosure);
}

function initializeSelectionForms() {
  for (const form of document.querySelectorAll("[data-selection-form]")) {
    const submitButton = form.querySelector("[data-selection-submit]");
    if (!submitButton) continue;

    const updateSubmitState = () => {
      submitButton.disabled = !form.querySelector('input[type="checkbox"]:checked');
    };
    form.addEventListener("change", updateSubmitState);
    updateSubmitState();
  }
}

function initializeAutomaticRefresh() {
  for (const element of document.querySelectorAll("[data-auto-refresh-ms]")) {
    const milliseconds = Number.parseInt(element.dataset.autoRefreshMs, 10);
    if (Number.isFinite(milliseconds) && milliseconds > 0) {
      window.setTimeout(() => window.location.reload(), milliseconds);
    }
  }
}

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-disclosure-target]")) updateDisclosure(event.target);
  if (event.target.matches("[data-submit-on-change]")) event.target.form?.requestSubmit();
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    initializeDisclosures();
    initializeSelectionForms();
    initializeAutomaticRefresh();
  });
} else {
  initializeDisclosures();
  initializeSelectionForms();
  initializeAutomaticRefresh();
}

document.addEventListener("click", (event) => {
  if (event.target instanceof HTMLDialogElement) {
    const bounds = event.target.getBoundingClientRect();
    const inside =
      event.clientX >= bounds.left &&
      event.clientX <= bounds.right &&
      event.clientY >= bounds.top &&
      event.clientY <= bounds.bottom;
    if (!inside) event.target.close();
  }
});
