function controlledMenu(button) {
  return document.getElementById(button.dataset.responsiveMenuButton);
}

function isDesktopMenu(menu) {
  return window.matchMedia(menu.dataset.desktopMedia).matches;
}

function setMenuOpen(button, menu, isOpen) {
  menu.dataset.open = String(isOpen);
  button.setAttribute("aria-expanded", String(isOpen));
}

function initializeResponsiveMenus() {
  const buttons = [...document.querySelectorAll("[data-responsive-menu-button]")];

  for (const button of buttons) {
    const menu = controlledMenu(button);
    if (!menu) continue;

    setMenuOpen(button, menu, false);
    button.addEventListener("click", () => {
      setMenuOpen(button, menu, menu.dataset.open !== "true");
    });

    window.matchMedia(menu.dataset.desktopMedia).addEventListener("change", () => {
      setMenuOpen(button, menu, false);
    });
  }

  document.addEventListener("click", (event) => {
    for (const button of buttons) {
      const menu = controlledMenu(button);
      if (!menu || isDesktopMenu(menu)) continue;
      if (!button.contains(event.target) && !menu.contains(event.target)) {
        setMenuOpen(button, menu, false);
      }
    }

    for (const dropdown of document.querySelectorAll("details[data-dropdown][open]")) {
      if (!dropdown.contains(event.target)) dropdown.removeAttribute("open");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;

    for (const button of buttons) {
      const menu = controlledMenu(button);
      if (menu && !isDesktopMenu(menu)) setMenuOpen(button, menu, false);
    }

    for (const dropdown of document.querySelectorAll("details[data-dropdown][open]")) {
      dropdown.removeAttribute("open");
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeResponsiveMenus);
} else {
  initializeResponsiveMenus();
}
