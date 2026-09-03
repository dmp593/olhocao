document.addEventListener("DOMContentLoaded", () => {
  const editor = document.querySelector("[data-legal-document-editor]");
  if (!editor) return;

  const sectionsContainer = editor.querySelector("[data-legal-sections]");
  const sectionTemplate = document.getElementById("legal-section-template");
  const clauseTemplate = document.getElementById("legal-clause-template");
  const totalSectionsInput = document.getElementById("id_sections-TOTAL_FORMS");

  function integerValue(input) {
    return Number.parseInt(input.value, 10) || 0;
  }

  function renderTemplate(template, replacements) {
    let markup = template.innerHTML;
    for (const [token, value] of Object.entries(replacements)) {
      markup = markup.replaceAll(token, String(value));
    }

    const wrapper = document.createElement("template");
    wrapper.innerHTML = markup.trim();
    return wrapper.content.firstElementChild;
  }

  function visibleChildren(container, selector) {
    return [...container.querySelectorAll(`:scope > ${selector}`)].filter(
      (element) => !element.hidden,
    );
  }

  function updateOrderFields(container, selector, numberSelector = null) {
    visibleChildren(container, selector).forEach((element, index) => {
      const orderInput = element.querySelector('input[name$="-order"]');
      if (orderInput) orderInput.value = index;
      if (numberSelector) element.querySelector(numberSelector).textContent = index + 1;
    });
  }

  function addSection() {
    const sectionIndex = integerValue(totalSectionsInput);
    const visibleSectionCount = visibleChildren(sectionsContainer, "[data-legal-section]").length;
    const section = renderTemplate(sectionTemplate, {
      __prefix__: sectionIndex,
      __section__: sectionIndex,
      __number__: visibleSectionCount + 1,
    });

    sectionsContainer.append(section);
    totalSectionsInput.value = sectionIndex + 1;
    section.querySelector('input:not([type="hidden"])')?.focus();
    updateOrderFields(sectionsContainer, "[data-legal-section]", "[data-section-number]");
  }

  function addClause(section) {
    const sectionIndex = section.dataset.sectionIndex;
    const totalClausesInput = document.getElementById(
      `id_lineitems-${sectionIndex}-TOTAL_FORMS`,
    );
    const clauseIndex = integerValue(totalClausesInput);
    const clause = renderTemplate(clauseTemplate, {
      __section__: sectionIndex,
      __prefix__: clauseIndex,
    });
    const clausesContainer = section.querySelector("[data-legal-clauses]");

    clausesContainer.append(clause);
    totalClausesInput.value = clauseIndex + 1;
    clause.querySelector("textarea")?.focus();
    updateOrderFields(clausesContainer, "[data-legal-clause]");
  }

  function markForDeletion(element) {
    const deleteInput = element.querySelector('input[name$="-DELETE"]');
    if (!deleteInput) return;
    deleteInput.value = "on";
    element.hidden = true;
  }

  editor.addEventListener("click", (event) => {
    if (event.target.closest("[data-add-section]")) {
      addSection();
      return;
    }

    const section = event.target.closest("[data-legal-section]");
    if (!section) return;

    if (event.target.closest("[data-add-clause]")) {
      addClause(section);
      return;
    }

    if (event.target.closest("[data-remove-clause]")) {
      const clause = event.target.closest("[data-legal-clause]");
      markForDeletion(clause);
      updateOrderFields(section.querySelector("[data-legal-clauses]"), "[data-legal-clause]");
      return;
    }

    if (event.target.closest("[data-remove-section]")) {
      markForDeletion(section);
      updateOrderFields(sectionsContainer, "[data-legal-section]", "[data-section-number]");
    }
  });
});
