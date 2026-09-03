function toDateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function initializeBookingDates() {
  const startDateInput = document.getElementById("start_date");
  const endDateInput = document.getElementById("end_date");
  if (!startDateInput || !endDateInput) return;

  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);

  startDateInput.value ||= toDateInputValue(today);
  endDateInput.value ||= toDateInputValue(tomorrow);

  const updateCheckoutMinimum = () => {
    if (!startDateInput.value) return;

    const minimumCheckout = new Date(`${startDateInput.value}T12:00:00`);
    minimumCheckout.setDate(minimumCheckout.getDate() + 1);
    const minimumValue = toDateInputValue(minimumCheckout);
    endDateInput.min = minimumValue;
    if (!endDateInput.value || endDateInput.value < minimumValue) {
      endDateInput.value = minimumValue;
    }
  };

  startDateInput.addEventListener("change", updateCheckoutMinimum);
  updateCheckoutMinimum();
}

function initializePetSelection() {
  const selectAll = document.getElementById("select-all-pets");
  if (!selectAll) return;

  const petCheckboxes = [...document.querySelectorAll('input[name="pet_ids"]')];
  selectAll.addEventListener("change", () => {
    for (const checkbox of petCheckboxes) checkbox.checked = selectAll.checked;
  });
  for (const checkbox of petCheckboxes) {
    checkbox.addEventListener("change", () => {
      selectAll.checked = petCheckboxes.every((item) => item.checked);
      selectAll.indeterminate = !selectAll.checked && petCheckboxes.some((item) => item.checked);
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initializeBookingDates();
  initializePetSelection();
});
