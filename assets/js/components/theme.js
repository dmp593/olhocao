(() => {
  "use strict";

  const STORAGE_KEY = "olhocao-theme";
  const DEFAULT_PREFERENCE = "auto";
  const VALID_PREFERENCES = new Set(["light", "dark", "auto"]);
  const root = document.documentElement;
  const darkModeMedia = window.matchMedia("(prefers-color-scheme: dark)");

  function normalizePreference(value) {
    return VALID_PREFERENCES.has(value) ? value : DEFAULT_PREFERENCE;
  }

  function readPreference() {
    try {
      return normalizePreference(window.localStorage.getItem(STORAGE_KEY));
    } catch (_error) {
      return DEFAULT_PREFERENCE;
    }
  }

  function persistPreference(preference) {
    try {
      window.localStorage.setItem(STORAGE_KEY, preference);
    } catch (_error) {
      // Storage may be unavailable in privacy-restricted browsing contexts.
    }
  }

  function resolveTheme(preference) {
    if (preference === "auto") return darkModeMedia.matches ? "dark" : "light";
    return preference;
  }

  function updateControls(preference) {
    for (const option of document.querySelectorAll("[data-theme-option]")) {
      option.setAttribute("aria-pressed", String(option.dataset.themeOption === preference));
    }
  }

  function applyPreference(preference, { persist = false } = {}) {
    const normalizedPreference = normalizePreference(preference);
    const resolvedTheme = resolveTheme(normalizedPreference);

    root.dataset.themePreference = normalizedPreference;
    root.dataset.themeResolved = resolvedTheme;
    root.style.colorScheme = resolvedTheme;

    const themeColor = document.querySelector('meta[name="theme-color"]');
    themeColor?.setAttribute("content", resolvedTheme === "dark" ? "#141922" : "#ffffff");

    if (persist) persistPreference(normalizedPreference);
    updateControls(normalizedPreference);
    window.dispatchEvent(
      new CustomEvent("olhocao:themechange", {
        detail: { preference: normalizedPreference, resolvedTheme },
      }),
    );
  }

  function initializeThemePickers() {
    updateControls(root.dataset.themePreference);
    document.addEventListener("click", (event) => {
      const option = event.target.closest("[data-theme-option]");
      if (!option) return;

      applyPreference(option.dataset.themeOption, { persist: true });
      option.closest("details")?.removeAttribute("open");
    });
  }

  applyPreference(readPreference());

  darkModeMedia.addEventListener("change", () => {
    if (root.dataset.themePreference === "auto") applyPreference("auto");
  });

  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY) applyPreference(event.newValue);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeThemePickers);
  } else {
    initializeThemePickers();
  }
})();
