const fs = require("node:fs/promises");
const { test, expect } = require("@playwright/test");

const baseURL = process.env.TEXTSQL_E2E_URL || "http://127.0.0.1:5000";
const executablePath = process.env.TEXTSQL_CHROMIUM;

test.use({
  baseURL,
  acceptDownloads: true,
  launchOptions: executablePath ? { executablePath } : {},
});

test("consulta real, lote reanudado y exportaciones", async ({ page }) => {
  test.setTimeout(240_000);
  const runId = `e2e_${Date.now()}`;
  const attempts = 12;
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/");
  await page.locator('[name="quantization"]').selectOption("Q4_K_M");
  await page.locator('[name="save"]').uncheck();
  await page.locator("#case-select").selectOption("P01");
  await page.getByRole("button", { name: "Generar SQL y consultar" }).click();
  await expect(page.locator("#rows tbody")).toContainText("3000", { timeout: 90_000 });
  await expect(page.locator("#gold-rows tbody")).toContainText("3000");
  await expect(page.locator("#verdict")).toContainText("coinciden");

  await page.getByText("Explorar el esquema seleccionado").click();
  await page.getByRole("button", { name: "Cargar tablas y relaciones" }).click();
  await expect(page.locator("#schema-view")).toContainText("7 tablas");

  await page.goto("/experimentos");
  for (const option of await page.locator('[name="quantizations"]').all()) {
    await option.setChecked((await option.getAttribute("value")) === "Q4_K_M");
  }
  await page.locator('[name="schemas"][value="alta"]').uncheck();
  await page.locator("#pilot-count").fill("3");
  await page.locator('[name="repetitions"]').fill("4");
  await page.locator('[name="run_id"]').fill(runId);
  await expect(page.locator("#total-attempts")).toHaveText(String(attempts));
  await page.getByRole("button", { name: "Iniciar experimento" }).click();
  const progress = page.locator("#experiment-message");
  await expect(progress).toContainText(/\d+ de 12/, { timeout: 90_000 });
  await page.getByRole("button", { name: "Detener después del intento actual" }).click();
  await expect(progress).toContainText("Detenida", { timeout: 45_000 });
  await page.reload();
  await page.getByRole("row").filter({ hasText: runId }).getByRole("button", { name: "Reanudar" }).click();
  await expect(progress).toContainText(`${attempts}/${attempts} intentos`, { timeout: 90_000 });
  await expect(progress).toContainText("Finalizada");

  await page.locator("#experiment-results").click();
  await expect(page.locator('[name="run"]')).toHaveValue(runId);
  await expect(page.locator("#result-kpis .kpi").first()).toContainText(String(attempts));
  await expect(page.locator("#chart-0 .main-svg").first()).toBeVisible();
  await page.getByRole("tab", { name: "Tabla factorial" }).click();
  await expect(page.locator("#factorial tbody")).toContainText("Q4_K_M");
  await page.getByRole("tab", { name: "Intentos y SQL" }).click();
  await expect(page.locator("#attempts > details")).toHaveCount(attempts);

  for (const [label, filename] of [["CSV", "intentos.csv"], ["JSON", "manifiesto.json"], ["Informe", "resumen.md"]]) {
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("link", { name: label, exact: true }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(filename);
    expect(await download.failure()).toBeNull();
    expect(await fs.readFile(await download.path(), "utf8")).toContain(runId);
  }
  expect(errors).toEqual([]);
});

test("navegación y ancho en móvil", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/", "/experimentos", "/resultados"]) {
    await page.goto(path);
    await expect(page.locator("main h1")).toBeVisible();
    if (path === "/resultados") {
      await expect(page.locator("#chart-0 .main-svg").first()).toBeVisible();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  await page.getByRole("button", { name: "Mostrar navegación" }).click();
  await page.getByRole("link", { name: /Demostración/ }).click();
  await expect(page.locator("main h1")).toHaveText("Demostración");
});
