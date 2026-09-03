document.addEventListener("DOMContentLoaded", () => {
  const startDateInput = document.getElementById("id_start_date");
  const endDateDisplay = document.getElementById("calculated-end-date");
  const durationInput = document.getElementById("duration-days");
  if (!startDateInput || !endDateDisplay || !durationInput) return;

  const duration = Number.parseInt(durationInput.value, 10);
  const updateEndDate = () => {
    if (!startDateInput.value || !Number.isFinite(duration)) {
      endDateDisplay.textContent = "—";
      return;
    }

    const endDate = new Date(`${startDateInput.value}T12:00:00`);
    endDate.setDate(endDate.getDate() + duration);
    endDateDisplay.textContent = new Intl.DateTimeFormat(document.documentElement.lang, {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(endDate);
  };

  startDateInput.addEventListener("change", updateEndDate);
  updateEndDate();
});
