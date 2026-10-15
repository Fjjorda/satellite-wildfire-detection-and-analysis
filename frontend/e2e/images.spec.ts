import { expect, test } from '@playwright/test';
import type { Download, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { strFromU8, unzipSync } from 'fflate';
import { resolve } from 'node:path';
import { jobSchema } from '../src/domain';

async function readDownload(download: Download): Promise<Buffer> {
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks);
}

async function uploadAndAnalyze(page: Page, clear = false, keyboard = false) {
  const files = clear ? resolve('../out/browser-tests/clear.png') : [resolve('../out/browser-tests/swir.png'), resolve('../out/browser-tests/truecolor.png')];
  if (keyboard) {
    await page.getByRole('button', { name: 'Seleccionar imágenes', exact: true }).focus();
    const chooser = page.waitForEvent('filechooser');
    await page.keyboard.press('Enter');
    await (await chooser).setFiles(files);
  } else await page.locator('input[type="file"]').setInputFiles(files);
  await expect(page.getByRole('button', { name: 'Analizar imágenes' })).toBeEnabled();
  // The True Color upload must finish before the first run.
  if (!clear) await expect(page.getByRole('combobox', { name: 'Tipo de composición de truecolor.png' })).toBeVisible();
  await page.getByRole('button', { name: 'Analizar imágenes' }).click();
  await expect(page.getByRole('heading', { name: 'Explora la escena' })).toBeVisible();
}

test('real image flow supports layers, comparison, languages, themes, and every export', async ({ page }) => {
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') browserErrors.push(message.text()); });
  await page.goto('/');
  await uploadAndAnalyze(page);
  await expect(page.getByText('Área quemada detectada', { exact: true })).toBeVisible();
  const layer = page.getByRole('checkbox', { name: 'Quemado confirmado' });
  await layer.uncheck();
  await expect(page.locator('img.image-layer[src*="layer-burned"]')).toHaveCount(0);
  await layer.check();
  await page.getByRole('switch', { name: 'Comparar original y resultado' }).check();
  await page.getByRole('slider', { name: 'Posición del comparador' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('slider', { name: 'Posición del comparador' })).toHaveValue('51');
  await page.getByRole('button', { name: 'Ampliar imagen' }).click();
  await expect(page.locator('.zoom-controls output')).toHaveText('150%');
  await page.getByRole('button', { name: 'Ajustar imagen' }).click();
  await page.getByRole('combobox', { name: 'Simulación visual' }).selectOption('deuteranopia');
  await expect(page.locator('.image-stack')).toHaveCSS('filter', /deuteranopia/);

  const report = page.getByRole('button', { name: 'Informe JSON', exact: true });
  const download = page.waitForEvent('download'); await report.click();
  const jsonDownload = await download;
  expect(jsonDownload.suggestedFilename()).toContain('.json');
  const stream = await jsonDownload.createReadStream();
  let contents = ''; for await (const chunk of stream!) contents += chunk.toString();
  expect(JSON.parse(contents).burned_pixels).toBeGreaterThan(0);
  expect(contents).not.toMatch(/"bbox"|latitude|longitude|geojson/);

  for (const name of ['Imagen PNG', 'Informe PDF', 'Tabla CSV', 'Descripción TXT']) {
    const waiting = page.waitForEvent('download', { timeout: 60_000 });
    await page.getByRole('button', { name, exact: true }).click();
    const file = await waiting;
    expect(await file.failure()).toBeNull();
    if (name === 'Informe PDF') {
      const pdfStream = await file.createReadStream();
      const buffers: Buffer[] = []; for await (const chunk of pdfStream!) buffers.push(Buffer.from(chunk));
      const pdf = Buffer.concat(buffers).toString('latin1');
      expect(pdf.startsWith('%PDF')).toBe(true);
      expect(pdf).toContain('/StructTreeRoot');
      expect(pdf).toContain('/Lang (es)');
      expect(pdf).toContain('/FontFile2');
      expect(pdf).toContain('/Alt');
    }
  }
  await page.getByRole('combobox', { name: 'Idioma', exact: true }).selectOption('en');
  await expect(page.getByRole('heading', { name: 'Explore the scene' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('#result-description')).toContainText('A burned area');
  for (const appearance of ['dark', 'contrast', 'light']) {
    await page.getByRole('combobox', { name: 'Appearance' }).selectOption(appearance);
    const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(accessibility.violations).toEqual([]);
  }
  await page.screenshot({ path: 'test-results/desktop-results.png', fullPage: true });
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.getByRole('combobox', { name: 'Appearance' })).toHaveValue('light');
  expect(browserErrors).toEqual([]);
});

test('mobile and keyboard navigation remain usable and handle invalid uploads', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Saltar al contenido' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main')).toBeFocused();
  await page.locator('input[type="file"]').setInputFiles({ name: 'broken.png', mimeType: 'image/png', buffer: Buffer.from('invalid') });
  await expect(page.getByText('El archivo no es una imagen PNG o JPG válida.').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Analizar imágenes' })).toBeDisabled();
  await uploadAndAnalyze(page, false, true);
  for (const width of [320, 375, 768]) {
    await page.setViewportSize({ width, height: 812 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.setViewportSize({ width: 375, height: 812 });
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.screenshot({ path: 'test-results/mobile-results.png', fullPage: true });
});

test('a scene without fire returns a complete empty result', async ({ page }) => {
  await page.goto('/');
  await uploadAndAnalyze(page, true);
  await expect(page.getByText('Sin área quemada detectada', { exact: true })).toBeVisible();
  await page.locator('.hotspots-disclosure > summary').click();
  await expect(page.getByText('No hay focos activos aceptados.', { exact: true })).toBeVisible();
  await expect(page.locator('#result-description')).toContainText('No se ha detectado');
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(accessibility.violations).toEqual([]);
});

test('True Color alone can be submitted with smoke recovery always enabled', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'FireSentinel', exact: true })).toBeVisible();
  await expect(page).toHaveTitle(/FireSentinel/);
  await expect(page.getByText('DEL PÍXEL A LA EVIDENCIA', { exact: true })).toHaveCount(0);
  await expect(page.locator('.composition-guide .tiny-badge').filter({ hasText: 'Recomendada' })).toHaveCount(2);
  await page.getByText('Opciones de análisis', { exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Resolución máxima', exact: true })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Sensibilidad del quemado' })).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: 'Estimar quemado probable bajo humo' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Analizar imágenes' })).toBeDisabled();
  await page.locator('input[type="file"]').setInputFiles(resolve('../out/browser-tests/truecolor.png'));
  const assignment = page.getByRole('combobox', { name: 'Tipo de composición de truecolor.png' });
  await assignment.selectOption('truecolor');
  await expect(page.getByRole('button', { name: 'Analizar imágenes' })).toBeEnabled();

  const submitted = page.waitForRequest(request => request.url().endsWith('/api/analyses') && request.method() === 'POST');
  await page.getByRole('button', { name: 'Analizar imágenes' }).click();
  const payload: unknown = (await submitted).postDataJSON();
  expect(payload).toEqual({ truecolor: expect.any(String), max_side: 2500, smoke_recovery: true });
  await expect(page.getByRole('heading', { name: 'Explora la escena' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Imagen base', exact: true })).toHaveValue('truecolor');
  await expect.poll(() => page.locator('.base-image').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByText('El análisis usa solo True Color.', { exact: false })).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(accessibility.violations).toEqual([]);
});

test('hotspots collapse by default and reopen with the keyboard', async ({ page }) => {
  await page.goto('/');
  await uploadAndAnalyze(page);
  const disclosure = page.locator('.hotspots-disclosure');
  const table = page.getByRole('table', { name: 'Focos aceptados en el análisis' });
  await expect(disclosure).not.toHaveAttribute('open');
  await expect(table).not.toBeVisible();
  await disclosure.locator(':scope > summary').focus();
  await page.keyboard.press('Enter');
  await expect(table).toBeVisible();
  const selected = page.getByRole('button', { name: /Ver foco \d+/ }).first();
  await selected.click();
  await expect(selected).toHaveAttribute('aria-pressed', 'true');
  await disclosure.locator(':scope > summary').focus();
  await page.keyboard.press('Space');
  await expect(table).not.toBeVisible();
});

test('download language is independent and ZIP retains every resource including hidden layers', async ({ page }) => {
  await page.goto('/');
  await uploadAndAnalyze(page);
  await page.getByRole('checkbox', { name: 'Agua excluida', exact: true }).uncheck();
  const language = page.getByRole('combobox', { name: 'Idioma de descarga', exact: true });
  await language.selectOption('en');
  await expect(page.getByRole('heading', { name: 'Llévate los resultados' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'es');

  const source = await page.locator('.base-image').getAttribute('src');
  const id = source!.match(/\/api\/analyses\/([^/]+)\//)![1]!;
  const job = jobSchema.parse(await (await page.request.get(`/api/analyses/${id}`)).json());
  const expected = job.result!;
  const download = page.waitForEvent('download', { timeout: 60_000 });
  await page.getByRole('button', { name: 'Descargar todo (.zip)', exact: true }).click();
  const zip = await download;
  expect(zip.suggestedFilename()).toBe('firesentinel-resources-en.zip');
  const files = unzipSync(await readDownload(zip));
  const report = JSON.parse(strFromU8(files['firesentinel-result-en.json']!));
  expect(report.language).toBe('en');
  expect(report.description).toContain('A burned area');
  expect(report.burned_pixels).toBe(expected.report.burned_pixels);
  expect(strFromU8(files['firesentinel-result-en.csv']!)).toContain('Confidence');
  expect(strFromU8(files['firesentinel-result-en.txt']!)).toContain('A burned area');
  expect(strFromU8(files['README-en.txt']!)).toContain('White (255)');
  expect(Buffer.from(files['firesentinel-result-en.pdf']!).toString('latin1')).toContain('/Lang (en)');
  expect(Buffer.from(files['firesentinel-result-en.png']!).subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  for (const name of expected.files) {
    const folder = name.startsWith('mask-') ? 'masks' : name.startsWith('base-') ? 'compositions' : name.startsWith('layer-') ? 'layers' : 'analysis';
    expect(files[`${folder}/${name}`]?.length).toBeGreaterThan(0);
  }
  expect(strFromU8(files['layers/layer-water.svg']!)).toContain('<svg');
  const mask = await page.request.get(`/api/analyses/${id}/files/mask-water.png`);
  expect(Buffer.from(files['masks/mask-water.png']!)).toEqual(await mask.body());

  await language.selectOption('es');
  const jsonDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Informe JSON', exact: true }).click();
  const spanish = JSON.parse((await readDownload(await jsonDownload)).toString());
  expect(spanish.language).toBe('es');
  expect(spanish.description).toContain('Se detecta');
  await page.getByText('Máscaras binarias', { exact: true }).click();
  await expect(page.getByText('Blanco (255): píxeles de esa categoría. Negro (0): resto de la imagen.', { exact: true })).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.setViewportSize({ width: 320, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/export-mobile.png', fullPage: true });
});

test('an incomplete archive fails without downloading a partial ZIP and permits retry', async ({ page }) => {
  await page.goto('/');
  await uploadAndAnalyze(page);
  const downloads: string[] = [];
  page.on('download', download => downloads.push(download.suggestedFilename()));
  await page.route('**/files/mask-water.png', route => route.fulfill({ status: 503, body: 'Unavailable' }));
  const button = page.getByRole('button', { name: 'Descargar todo (.zip)', exact: true });
  await button.click();
  await expect(page.getByText('No se pudo generar el archivo. Revisa la conexión e inténtalo de nuevo.', { exact: true }).first()).toBeVisible();
  await expect(button).toBeEnabled();
  expect(downloads).toEqual([]);
  await page.unroute('**/files/mask-water.png');
  const retried = page.waitForEvent('download', { timeout: 60_000 });
  await button.click();
  expect((await retried).suggestedFilename()).toBe('firesentinel-resources-es.zip');
});
