"use strict";

// Se ejecuta antes del CSS para aplicar el tema correcto desde el primer dibujo.
(() => {
  const storageKey = "text-to-sql-theme";
  const root = document.documentElement;
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const systemTheme = () => (system.matches ? "dark" : "light");
  let preference;
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === "light" || saved === "dark") preference = saved;
  } catch {
    // La selección sigue disponible cuando el navegador bloquea el almacenamiento.
  }
  root.dataset.theme = preference || systemTheme();

  document.addEventListener("DOMContentLoaded", () => {
    const picker = document.querySelector(".theme-picker");
    const buttons = picker.querySelectorAll("[data-theme-choice]");
    const updateButtons = () => {
      for (const button of buttons) {
        button.setAttribute("aria-pressed", button.dataset.themeChoice === root.dataset.theme);
      }
    };
    const apply = (theme) => {
      if (theme === root.dataset.theme) return;
      root.dataset.theme = theme;
      updateButtons();
      document.dispatchEvent(new Event("lab:themechange"));
    };

    updateButtons();
    picker.hidden = false;
    system.addEventListener("change", () => {
      if (!preference) apply(systemTheme());
    });
    for (const button of buttons) {
      button.addEventListener("click", () => {
        preference = button.dataset.themeChoice;
        try {
          localStorage.setItem(storageKey, preference);
        } catch {
          // La elección permanece activa en esta página.
        }
        apply(preference);
      });
    }
  });
})();
