function normalizedQuantity(input, whileTyping = false) {
  const maximum = Number.parseInt(input.max, 10);
  const value = Number.parseInt(input.value, 10) || 0;

  if (value < 0) input.value = "0";
  if (value > maximum && (!whileTyping || String(value).length > String(maximum).length)) {
    input.value = String(maximum);
  }
  return Number.parseInt(input.value, 10) || 0;
}

function updateServiceCard(card) {
  const selected = [...card.querySelectorAll("[data-service-quantity]")]
    .some((input) => Number.parseInt(input.value, 10) > 0);
  card.classList.toggle("ring-2", selected);
  card.classList.toggle("ring-accent-300", selected);
}

function updateSchedulingFields(selection) {
  const quantity = selection.querySelector("[data-service-quantity]");
  const schedule = selection.querySelector("[data-schedule-preference]");
  const date = selection.querySelector("[data-preferred-date]");
  const time = selection.querySelector("[data-preferred-time]");
  const dateWrapper = selection.querySelector("[data-preferred-date-wrapper]");
  const timeWrapper = selection.querySelector("[data-preferred-time-wrapper]");
  if (!quantity || !schedule || !date || !time) return;

  const hasService = Number.parseInt(quantity.value, 10) > 0;
  schedule.disabled = !hasService;
  if (!hasService) {
    schedule.value = "unscheduled";
    date.value = "";
    time.value = "";
  }

  const wantsDate = hasService && ["date", "date_time"].includes(schedule.value);
  const wantsTime = hasService && schedule.value === "date_time";
  date.disabled = !wantsDate;
  time.disabled = !wantsTime;
  dateWrapper.hidden = !wantsDate;
  timeWrapper.hidden = !wantsTime;
  if (!wantsDate) date.value = "";
  if (!wantsTime) time.value = "";
}

document.addEventListener("DOMContentLoaded", () => {
  for (const selection of document.querySelectorAll("[data-service-selection]")) {
    const quantity = selection.querySelector("[data-service-quantity]");
    const schedule = selection.querySelector("[data-schedule-preference]");
    const card = selection.closest("[data-service-row]");
    if (!quantity || !schedule || !card) continue;

    updateSchedulingFields(selection);
    updateServiceCard(card);
    quantity.addEventListener("input", () => normalizedQuantity(quantity, true));
    quantity.addEventListener("change", () => {
      normalizedQuantity(quantity);
      updateSchedulingFields(selection);
      updateServiceCard(card);
    });
    schedule.addEventListener("change", () => updateSchedulingFields(selection));
  }
});
