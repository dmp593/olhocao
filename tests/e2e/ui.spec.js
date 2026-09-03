const { expect, test } = require("@playwright/test");

const customerCredentials = {
  username: "e2e-customer",
  password: "test-password-123",
};
const administratorCredentials = {
  username: "e2e-admin",
  password: "e2e-admin-password",
};
const operatorCredentials = {
  username: "e2e-operator",
  password: "e2e-operator-password",
};

async function login(page, credentials, next = "") {
  const loginPath = next ? `/accounts/login/?next=${encodeURIComponent(next)}` : "/accounts/login/";
  await page.goto(loginPath);
  await page.getByLabel("Username").fill(credentials.username);
  await page.getByLabel("Password").fill(credentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function expectConsistentPage(page, { patterned = true } = {}) {
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.locator("main")).toBeVisible();

  const problems = await page.evaluate(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden";
    };
    const controls = [...document.querySelectorAll("input, select, textarea")]
      .filter((control) => control.type !== "hidden" && visible(control));
    const unlabeledControls = controls.filter((control) => {
      const id = control.id;
      return !(
        control.getAttribute("aria-label") ||
        control.getAttribute("aria-labelledby") ||
        control.closest("label") ||
        (id && document.querySelector(`label[for="${CSS.escape(id)}"]`))
      );
    });
    const oversizedCheckboxes = controls
      .filter((control) => control.type === "checkbox")
      .filter((control) => {
        const box = control.getBoundingClientRect();
        return box.width > 36 || box.height > 36;
      });
    const oversizedActions = [...document.querySelectorAll(".ui-btn")]
      .filter(visible)
      .filter((action) => action.getBoundingClientRect().height > 72);
    const cardsWithoutSpacing = [...document.querySelectorAll(".ui-card")]
      .filter(visible)
      .filter((card) => {
        if (Number.parseFloat(getComputedStyle(card).paddingLeft) >= 16) return false;
        return ![...card.children].some(
          (child) => Number.parseFloat(getComputedStyle(child).paddingLeft) >= 16,
        );
      });
    return {
      horizontalOverflow:
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      unlabeledControls: unlabeledControls.map((control) => control.outerHTML),
      oversizedCheckboxes: oversizedCheckboxes.map((control) => control.outerHTML),
      oversizedActions: oversizedActions.map((action) => action.outerHTML),
      cardsWithoutSpacing: cardsWithoutSpacing.map((card) => card.outerHTML),
      usesBrandTypeface: getComputedStyle(document.body).fontFamily.includes("Bricolage Grotesque"),
    };
  });

  expect(problems).toEqual({
    horizontalOverflow: false,
    unlabeledControls: [],
    oversizedCheckboxes: [],
    oversizedActions: [],
    cardsWithoutSpacing: [],
    usesBrandTypeface: true,
  });
  if (patterned) {
    await expect(page.locator(".ui-patterned-page .bg-paws").first()).toBeAttached();
  }
}

async function visitAndAudit(page, path, options = {}) {
  const response = await page.goto(path);
  expect(response, `No HTTP response received for ${path}`).not.toBeNull();
  expect(response.status(), `${path} returned HTTP ${response.status()}`).toBeLessThan(400);
  await expectConsistentPage(page, options);
}

async function expectCatalogBadgesToRemainLegible(catalogCard) {
  const catalogBadges = catalogCard.locator("[data-catalog-card-badges] .ui-badge");
  await expect(catalogBadges).toHaveCount(2);

  const badgeLayout = await catalogBadges.evaluateAll((badges) => {
    const rectangles = badges.map((badge) => badge.getBoundingClientRect());
    const countBadge = badges.at(-1);
    const countBadgeStyle = getComputedStyle(countBadge);
    const countBadgeLineHeight = Number.parseFloat(countBadgeStyle.lineHeight);

    return {
      badgesDoNotOverlap: rectangles.every((rectangle, index) =>
        rectangles.slice(index + 1).every(
          (otherRectangle) =>
            rectangle.right <= otherRectangle.left ||
            otherRectangle.right <= rectangle.left ||
            rectangle.bottom <= otherRectangle.top ||
            otherRectangle.bottom <= rectangle.top,
        ),
      ),
      badgesKeepNaturalWidth: badges.every(
        (badge) => getComputedStyle(badge).flexShrink === "0",
      ),
      countBadgeUsesOneLine: countBadge.getBoundingClientRect().height <= countBadgeLineHeight + 10,
    };
  });

  expect(badgeLayout).toEqual({
    badgesDoNotOverlap: true,
    badgesKeepNaturalWidth: true,
    countBadgeUsesOneLine: true,
  });
}

test.beforeEach(async ({ page }) => {
  const browserErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.browserErrors = browserErrors;
});

test.afterEach(async ({ page }) => {
  expect(page.browserErrors).toEqual([]);
});

test("public navigation, offer cards and authentication return path", async ({ page }) => {
  await page.goto("/activities/");
  await expectConsistentPage(page, { patterned: false });
  await expect(page.getByRole("heading", { name: "Daycare, training and obedience" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Buy" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Subscribe" })).toBeVisible();

  await page.getByRole("link", { name: "Subscribe" }).click();
  await expect(page).toHaveURL(/\/accounts\/login\/\?next=/);
  await page.getByLabel("Username").fill(customerCredentials.username);
  await page.getByLabel("Password").fill(customerCredentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/activities\/offers\/\d+\/purchase\/$/);
  await expect(page.getByRole("heading", { name: "Treino duas vezes por semana" })).toBeVisible();
});

test("public navigation survives resize, reload, history and delayed JavaScript", async ({
  browser,
  page,
}) => {
  const publicNavigation = page.locator("#public-navigation-menu");
  const hotelLink = publicNavigation.getByRole("link", { name: "Hotel" });

  for (const width of [1440, 768, 390, 2048, 767, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/pricing/");

    if (width >= 768) {
      await expect(publicNavigation).toBeVisible();
      await expect(hotelLink).toBeVisible();
    } else {
      await page.getByRole("button", { name: "Open navigation menu" }).click();
      await expect(publicNavigation).toBeVisible();
      await expect(hotelLink).toBeVisible();
    }
  }

  await page.goto("/about-us/");
  await page.goBack();
  await expect(publicNavigation).toBeVisible();
  await expect(hotelLink).toBeVisible();
  await page.reload();
  await expect(hotelLink).toBeVisible();

  const noJavaScriptContext = await browser.newContext({
    javaScriptEnabled: false,
    viewport: { width: 1440, height: 900 },
  });
  const noJavaScriptPage = await noJavaScriptContext.newPage();
  await noJavaScriptPage.goto("/pricing/");
  await expect(
    noJavaScriptPage.locator("#public-navigation-menu").getByRole("link", { name: "Hotel" }),
  ).toBeVisible();
  await noJavaScriptContext.close();
});

test("all public pages share the visual system and render without errors", async ({ page }) => {
  const publicPaths = [
    "/",
    "/pricing/",
    "/contact-us/",
    "/about-us/",
    "/terms-and-conditions/",
    "/privacy-policy/",
    "/activities/",
    "/accounts/login/",
    "/accounts/signup/",
    "/accounts/password-reset/",
  ];

  for (const path of publicPaths) {
    await visitAndAudit(page, path, { patterned: false });
  }
});

test("customer can navigate every portal and complete a one-night booking review", async ({ page }) => {
  await login(page, customerCredentials);
  const portalPaths = [
    "/accounts/",
    "/pets/",
    "/hotel/bookings/",
    "/activities/my/",
    "/activities/my/documents/",
    "/accounts/change/",
  ];
  for (const path of portalPaths) {
    await visitAndAudit(page, path);
    await expect(page.locator('nav[aria-label="Customer area"]')).toBeVisible();
  }

  await page.goto("/pets/add/");
  await expectConsistentPage(page);
  await expect(page.locator('select[name="species"]')).toHaveCount(0);
  await page.getByLabel("Name").fill("Pipoca");
  await page.getByLabel("Breed").fill("Labrador");
  await page.getByLabel("Gender").selectOption("F");
  await page.getByLabel("Birthdate").fill("2022-04-10");
  await page.getByRole("button", { name: "Add dog" }).click();
  await expect(page).toHaveURL("/pets/");
  await expect(page.getByText("Pipoca", { exact: true })).toBeVisible();

  await page.goto("/hotel/bookings/1/");
  await expectConsistentPage(page);
  await expect(page.getByText("Dog • Mixed", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Complete Payment" })).toBeVisible();

  await page.goto("/hotel/book-now/");
  await page.locator('label[for^="pet-"]').first().click();
  const startDate = new Date();
  startDate.setDate(startDate.getDate() + 10);
  const endDate = new Date(startDate);
  endDate.setDate(endDate.getDate() + 1);
  const dateValue = (value) => value.toISOString().slice(0, 10);
  await page.locator('#start_date').fill(dateValue(startDate));
  await page.locator('#end_date').fill(dateValue(endDate));
  await page.getByRole("button", { name: /Continue to Services/ }).click();
  await expect(page).toHaveURL("/hotel/book-now/services/");
  await expect(page.getByText("1 day", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Review booking/i }).click();
  await expect(page).toHaveURL("/hotel/book-now/review/");
  await expect(page.getByText("Dog • Mixed", { exact: true })).toBeVisible();
});

test("all customer self-service pages remain consistent and reachable", async ({ page }) => {
  await login(page, customerCredentials);
  const customerPaths = [
    "/accounts/password-change/",
    "/pets/1/",
    "/pets/1/edit/",
    "/pets/1/delete/",
    "/hotel/bookings/1/modify/",
    "/hotel/bookings/1/cancel/",
    "/activities/sessions/",
    "/activities/offers/1/purchase/",
    "/activities/offers/2/purchase/",
  ];

  for (const path of customerPaths) {
    await visitAndAudit(page, path);
    await expect(page.locator('nav[aria-label="Customer area"]')).toBeVisible();
  }
});

test("cancelled booking never exposes a stale payment action", async ({ page }) => {
  await login(page, customerCredentials);
  await page.goto("/hotel/bookings/2/");
  await expectConsistentPage(page);
  await expect(page.getByText("Booking cancelled", { exact: true })).toBeVisible();
  await expect(page.getByText("No further payment is required.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Complete Payment" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Try payment again" })).toHaveCount(0);
});

test("administrator navigation stays inside management and exposes developer admin separately", async ({ page }) => {
  await login(page, administratorCredentials);
  const managementPaths = [
    "/backoffice/dashboard/",
    "/backoffice/users/",
    "/backoffice/activities/",
    "/backoffice/activities/calendar/",
    "/backoffice/activities/trainers/",
    "/backoffice/activities/trainers/create/",
    "/backoffice/activities/schedules/",
    "/backoffice/activities/schedules/create/",
    "/backoffice/activities/closures/",
    "/backoffice/activities/closures/create/",
    "/backoffice/subscriptions/",
    "/backoffice/catalog/",
    "/backoffice/catalog/optional-services/",
    "/backoffice/catalog/types/create/",
    "/backoffice/catalog/projections/",
    "/backoffice/promotions/create/",
    "/backoffice/activities/sessions/create/",
    "/backoffice/users/2/",
    "/backoffice/users/2/edit/",
    "/backoffice/users/2/password/",
    "/backoffice/subscriptions/2/",
    "/backoffice/contacts-requests/",
    "/backoffice/legal-documents/",
    "/backoffice/legal-documents/create/",
    "/backoffice/site-content/",
    "/backoffice/site-content/settings/",
    "/backoffice/site-content/slides/",
    "/backoffice/site-content/slides/1/edit/",
    "/hotel/bookings/1/",
  ];
  for (const path of managementPaths) {
    await visitAndAudit(page, path);
    await expect(page.locator('nav[aria-label="Management navigation"]')).toBeVisible();
  }

  await page.goto("/hotel/bookings/");
  await expect(page).toHaveURL("/backoffice/dashboard/");
  await page.goto("/hotel/bookings/1/");
  await expect(page.getByRole("link", { name: "Send link for payment" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Hotel bookings" })).toBeVisible();
  await page.locator("details[data-dropdown]").last().locator("summary").click();
  const developerAdminLink = page.getByRole("link", { name: "Django administration" });
  await expect(developerAdminLink).toBeVisible();
  const menuItemGap = await developerAdminLink.evaluate((link) =>
    Number.parseFloat(getComputedStyle(link).columnGap),
  );
  expect(menuItemGap).toBeGreaterThanOrEqual(12);
});

test("administrator can understand prices, plans and centrally associate a reusable optional service", async ({
  page,
}) => {
  await login(page, administratorCredentials);
  await page.goto("/backoffice/catalog/");
  await page.getByRole("button", { name: "Ver em português" }).click();

  const portugueseDaycareCard = page.locator("article", { hasText: "ATL E2E" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await expectCatalogBadgesToRemainLegible(portugueseDaycareCard);
  await page.setViewportSize({ width: 390, height: 844 });
  await expectCatalogBadgesToRemainLegible(portugueseDaycareCard);

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "Ver em inglês" }).click();
  const daycareCard = page.locator("article", { hasText: "E2E daycare" });
  await daycareCard.getByRole("link", { name: "Manage structure" }).click();
  await expectConsistentPage(page);
  await expect(page.getByRole("heading", { name: "Levels, prices and plans" })).toBeVisible();
  await expect(page.getByText("A level defines operational rules", { exact: false })).toBeVisible();

  const levelItems = page.locator("[data-level-order-item]");
  await expect(levelItems).toHaveCount(2);
  await levelItems.first().getByRole("button", { name: "Reorder Normal" }).press("ArrowDown");
  await expect(page.getByRole("status")).toHaveText("Catalog level order saved.");
  await expect(levelItems.first().getByRole("heading", { name: "Premium E2E" })).toBeVisible();
  await page.reload();
  await expect(levelItems.first().getByRole("heading", { name: "Premium E2E" })).toBeVisible();

  const lastLevelBounds = await levelItems.last().boundingBox();
  const dragSaveResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/levels/order/"),
  );
  await levelItems
    .first()
    .getByRole("button", { name: "Reorder Premium E2E" })
    .dragTo(levelItems.last(), {
      targetPosition: {
        x: lastLevelBounds.width / 2,
        y: lastLevelBounds.height - 8,
      },
    });
  expect((await dragSaveResponse).ok()).toBe(true);
  await expect(levelItems.first().getByRole("heading", { name: "Normal" })).toBeVisible();
  await page.reload();
  await expect(levelItems.first().getByRole("heading", { name: "Normal" })).toBeVisible();

  await page.getByRole("link", { name: "Optional services" }).click();
  await expectConsistentPage(page);
  await expect(
    page.getByRole("heading", { name: "Optional services", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Service destination")).toHaveValue(/type:\d+/);
  await page.getByLabel("Select Bath and grooming").check();
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByRole("status")).toHaveText("You have unsaved changes.");
  await page.getByRole("button", { name: "Save optional services" }).click();

  await expect(page.getByText("Optional services updated.")).toBeVisible();
  await expect(page.getByText("Bath and grooming", { exact: true })).toBeVisible();
  await expect(page.getByText("Customers may add these services", { exact: false })).toBeVisible();
});

test("administrator can search large promotion audiences without loading them into the form", async ({
  page,
}) => {
  await login(page, administratorCredentials);
  await page.goto("/backoffice/promotions/create/");
  await expectConsistentPage(page);

  await page.getByLabel("Specific customers").fill("Joana");
  const customerResult = page.getByRole("option").filter({ hasText: "Joana Silva" });
  await expect(customerResult).toBeVisible();
  await customerResult.click();
  const customerPicker = page.locator("[data-async-multi-select]").filter({
    has: page.getByLabel("Specific customers"),
  });
  await expect(
    customerPicker.locator("[data-async-selected-item]", { hasText: "Joana Silva" }),
  ).toBeVisible();

  await page.getByLabel("Eligible breeds").fill("Mixed");
  const breedResult = page.getByRole("option").filter({ hasText: "Mixed" });
  await expect(breedResult).toBeVisible();
  await breedResult.click();
  const breedPicker = page.locator("[data-async-multi-select]").filter({
    has: page.getByLabel("Eligible breeds"),
  });
  await expect(
    breedPicker.locator("[data-async-selected-item]", { hasText: "Mixed" }),
  ).toBeVisible();
});

test("administrator can confirm a documented complimentary stay without a fake payment", async ({
  page,
}) => {
  await login(page, administratorCredentials);
  await page.goto("/hotel/bookings/1/price/");
  await expectConsistentPage(page);
  await page.getByLabel("New final price").fill("0.00");
  await page.getByLabel("Reason for the change").fill("E2E goodwill after a service complaint");
  await page.getByRole("button", { name: "Save final price" }).click();

  await expect(page).toHaveURL("/hotel/bookings/1/");
  await expect(page.getByText("The booking is confirmed as complimentary.")).toBeVisible();
  await expect(page.getByText("Confirmed complimentary stay")).toBeVisible();
  await expect(page.getByRole("link", { name: "Send link for payment" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Complete Payment" })).toHaveCount(0);
});

test("administrator uses audited cancellation pages for reservations and sessions", async ({
  page,
}) => {
  await login(page, administratorCredentials);

  await page.goto("/backoffice/activities/reservations/1/cancel/");
  await expectConsistentPage(page);
  await expect(page.getByRole("heading", { name: "Cancel activity reservation" })).toBeVisible();
  await expect(page.getByLabel("Return this use to the customer")).toBeChecked();
  await page
    .getByLabel("Cancellation reason")
    .fill("Customer requested an exceptional management cancellation");
  await page.getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(page).toHaveURL("/backoffice/activities/");
  await expect(page.getByText("Reservation cancelled and the use was returned.")).toBeVisible();

  await page.goto("/backoffice/activities/sessions/2/edit/");
  await expectConsistentPage(page);
  await expect(page.getByRole("heading", { name: "Cancel session" })).toBeVisible();
  await page.getByLabel("Cancellation reason").fill("Exceptional closure confirmed by management");
  await page.getByRole("button", { name: "Cancel session and notify customers" }).click();
  await expect(page).toHaveURL("/backoffice/activities/calendar/");
  await expect(
    page.getByText("Session cancelled; 1 customer reservations were released."),
  ).toBeVisible();

  await page.goto("/backoffice/activities/sessions/2/edit/");
  await expectConsistentPage(page);
  await expect(page.getByText("This session is cancelled.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save session" })).toHaveCount(0);
});

test("operator uses management but cannot access developer administration", async ({ page }) => {
  await login(page, operatorCredentials);
  await page.goto("/backoffice/dashboard/");
  await expectConsistentPage(page);
  await expect(page.locator('nav[aria-label="Management navigation"]')).toBeVisible();

  await page.locator("details[data-dropdown]").last().locator("summary").click();
  await expect(page.getByRole("link", { name: "Django administration" })).toHaveCount(0);

  await page.goto("/admin/");
  await expect(page).toHaveURL(/\/admin\/login\/\?next=\/admin\//);
});

test("administrator can switch language without leaving management", async ({ page }) => {
  await login(page, administratorCredentials);
  await page.goto("/backoffice/dashboard/?medical=medication");
  await page.getByRole("button", { name: "Ver em português" }).click();
  await expect(page).toHaveURL("/backoffice/dashboard/?medical=medication");
  await expect(page.getByRole("heading", { name: "Gestão do hotel" })).toBeVisible();
  await expect(page.locator('nav[aria-label="Navegação da gestão"]')).toBeVisible();
});

test("operator can update translated frontoffice content without changing its layout", async ({
  page,
}) => {
  await login(page, operatorCredentials);
  await page.goto("/backoffice/site-content/");
  await expectConsistentPage(page);
  await expect(page.getByRole("heading", { name: "Website content", exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Edit information" }).click();
  await expect(page).toHaveURL("/backoffice/site-content/settings/");
  await expectConsistentPage(page);
  await page.getByLabel("Pricing title (English)").fill("E2E managed pricing");
  await page.getByRole("button", { name: "Save information" }).click();
  await expect(page).toHaveURL("/backoffice/site-content/");
  await expect(page.getByText("Website settings updated.")).toBeVisible();

  await page.goto("/pricing/");
  await expect(page.getByRole("heading", { name: "E2E managed pricing" })).toBeVisible();
  await expectConsistentPage(page, { patterned: false });

  await page.goto("/backoffice/site-content/slides/1/edit/");
  await expect(page.getByText("Current image", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Image description (English)")).not.toHaveValue("");
});

test("administrator can create a customer and set an emergency password", async ({ page }) => {
  await login(page, administratorCredentials);
  await page.goto("/backoffice/users/create/");
  await page.getByLabel("First name").fill("Rita");
  await page.getByLabel("Last name").fill("Costa");
  await page.getByLabel("Email address").fill("rita.e2e@example.com");
  await page.getByLabel("Username").fill("rita-e2e");
  await page.getByLabel("Phone number").fill("912345678");
  await page.getByLabel("VAT number").fill("245716350");
  await page.getByRole("button", { name: "Create and send invitation" }).click();
  await expect(page).toHaveURL(/\/backoffice\/users\/\d+\/$/);
  const userId = page.url().match(/users\/(\d+)\/$/)[1];

  await page.goto(`/backoffice/users/${userId}/password/`);
  await expectConsistentPage(page);
  await page.getByLabel("New password", { exact: true }).fill("Rita-E2E-Only-8472");
  await page.getByLabel("New password confirmation").fill("Rita-E2E-Only-8472");
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page).toHaveURL(`/backoffice/users/${userId}/`);
  await expect(page.getByText("The customer's password was updated.")).toBeVisible();
});

test("administrator can build a legal document with dynamic sections and clauses", async ({ page }) => {
  await login(page, administratorCredentials);
  await page.goto("/backoffice/legal-documents/create/");
  await expectConsistentPage(page);

  await page.locator('select[name="doc_type"]').selectOption("privacy");
  await page.locator('input[name="effective_date"]').fill("2026-09-02");
  await page.locator('input[name="name_pt"]').fill("Privacidade E2E");
  await page.locator('input[name="name_en"]').fill("E2E Privacy");
  await page.locator('textarea[name="heading_pt"]').fill("Como protegemos os seus dados.");
  await page.locator('textarea[name="heading_en"]').fill("How we protect your data.");

  await page.getByRole("button", { name: "Add Section" }).click();
  const section = page.locator("[data-legal-section]");
  await expect(section).toHaveCount(1);
  await section.locator('input[name$="-title_pt"]').fill("Dados pessoais");
  await section.locator('input[name$="-title_en"]').fill("Personal data");
  await section.getByRole("button", { name: "Add Clause" }).click();
  await section.locator('textarea[name$="-text_pt"]').fill("Recolhemos apenas os dados necessários.");
  await section.locator('textarea[name$="-text_en"]').fill("We only collect necessary data.");
  await page.getByRole("button", { name: "Save Document" }).click();

  await expect(page).toHaveURL("/backoffice/legal-documents/");
  await expect(page.getByText("E2E Privacy", { exact: false })).toBeVisible();
});

test("management UI remains usable on a phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, administratorCredentials);
  await page.goto("/backoffice/activities/");
  await expectConsistentPage(page);
  await page.getByRole("button", { name: "Open management menu" }).click();
  await expect(page.getByRole("link", { name: "Catalog" })).toBeVisible();
  await page.getByRole("link", { name: "Catalog" }).click();
  await expect(page).toHaveURL("/backoffice/catalog/");
  await expectConsistentPage(page);
});
