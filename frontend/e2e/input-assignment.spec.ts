import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { jobSchema, uploadSchema } from '../src/domain';
import type { Composition } from '../src/domain';

async function seedFiles() {
  const [swir, truecolor] = await Promise.all([
    readFile(resolve('../out/browser-tests/swir.png')),
    readFile(resolve('../out/browser-tests/truecolor.png')),
  ]);
  return { swir, truecolor };
}

async function captureUploads(page: Page, detected: Composition) {
  const ids = new Map<string, string>();
  await page.route('**/api/uploads', async route => {
    const response = await route.fetch();
    const uploaded = uploadSchema.parse(await response.json());
    ids.set(uploaded.name, uploaded.id);
    await route.fulfill({ response, json: { ...uploaded, detected } });
  });
  return ids;
}

test('explicit filenames select all three types even when visual suggestions disagree', async ({ page }) => {
  const files = await seedFiles();
  const ids = await captureUploads(page, 'wildfire');
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles([
    { name: 'scene_SWIR.png', mimeType: 'image/png', buffer: files.swir },
    { name: 'scene_true_color.png', mimeType: 'image/png', buffer: files.truecolor },
    { name: 'scene_Wildfires.png', mimeType: 'image/png', buffer: files.truecolor },
  ]);
  for (const [name, type] of [['scene_SWIR.png', 'swir'], ['scene_true_color.png', 'truecolor'], ['scene_Wildfires.png', 'wildfire']]) {
    await expect(page.getByRole('combobox', { name: `Tipo de composición de ${name}`, exact: true })).toHaveValue(type!);
  }
  await expect(page.getByText('Por nombre: True Color', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Analizar imágenes', exact: true })).toBeEnabled();
  const submitted = page.waitForRequest(request => request.url().endsWith('/api/analyses') && request.method() === 'POST');
  await page.getByRole('button', { name: 'Analizar imágenes', exact: true }).click();
  expect((await submitted).postDataJSON()).toEqual({
    swir: ids.get('scene_SWIR.png'), truecolor: ids.get('scene_true_color.png'),
    wildfire: ids.get('scene_Wildfires.png'), max_side: 2500, smoke_recovery: true,
  });
  await expect(page.getByRole('heading', { name: 'Explora la escena' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Imagen base', exact: true }).getByRole('option', { name: 'Wildfire', exact: true })).toHaveCount(1);
});

test('duplicate visual guesses stay visible and manual types determine the submitted images', async ({ page }) => {
  const files = await seedFiles();
  const ids = await captureUploads(page, 'swir');
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles([
    { name: 'scene-a.png', mimeType: 'image/png', buffer: files.swir },
    { name: 'scene-b.png', mimeType: 'image/png', buffer: files.truecolor },
  ]);
  const first = page.getByRole('combobox', { name: 'Tipo de composición de scene-a.png', exact: true });
  const second = page.getByRole('combobox', { name: 'Tipo de composición de scene-b.png', exact: true });
  await expect(first).toHaveValue('swir');
  await expect(second).toHaveValue('swir');
  await expect(page.getByRole('button', { name: 'Analizar imágenes', exact: true })).toBeDisabled();
  await expect(page.getByText('Asigna una sola imagen a cada tipo de composición.', { exact: true })).toBeVisible();
  await second.selectOption('truecolor');
  await expect(page.getByText('Seleccionado: True Color', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Analizar imágenes', exact: true })).toBeEnabled();
  await first.selectOption('unused');
  await expect(page.getByRole('button', { name: 'Analizar imágenes', exact: true })).toBeDisabled();
  await first.selectOption('swir');
  const submitted = page.waitForRequest(request => request.url().endsWith('/api/analyses') && request.method() === 'POST');
  await page.getByRole('button', { name: 'Analizar imágenes', exact: true }).click();
  expect((await submitted).postDataJSON()).toEqual({ swir: ids.get('scene-a.png'), truecolor: ids.get('scene-b.png'), max_side: 2500, smoke_recovery: true });
  await expect(page.getByRole('heading', { name: 'Explora la escena' })).toBeVisible();
});

test('manual corrections override filenames and survive subsequent uploads', async ({ page }) => {
  const files = await seedFiles();
  const ids = await captureUploads(page, 'wildfire');
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({ name: 'true_color.png', mimeType: 'image/png', buffer: files.swir });
  const selected = page.getByRole('combobox', { name: 'Tipo de composición de true_color.png', exact: true });
  await expect(selected).toHaveValue('truecolor');
  await selected.selectOption('swir');
  await page.locator('input[type="file"]').setInputFiles({ name: 'natural.png', mimeType: 'image/png', buffer: files.truecolor });
  await page.getByRole('combobox', { name: 'Tipo de composición de natural.png', exact: true }).selectOption('truecolor');
  await expect(selected).toHaveValue('swir');
  await expect(page.getByText('Seleccionado: SWIR', { exact: true })).toBeVisible();
  const submitted = page.waitForRequest(request => request.url().endsWith('/api/analyses') && request.method() === 'POST');
  await page.getByRole('button', { name: 'Analizar imágenes', exact: true }).click();
  expect((await submitted).postDataJSON()).toEqual({ swir: ids.get('true_color.png'), truecolor: ids.get('natural.png'), max_side: 2500, smoke_recovery: true });
  await expect(page.getByRole('heading', { name: 'Explora la escena' })).toBeVisible();
});

test('a batch exceeding three files is rejected before upload', async ({ page }) => {
  const files = await seedFiles();
  let uploads = 0;
  page.on('request', request => { if (request.url().endsWith('/api/uploads')) uploads++; });
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles(['a', 'b', 'c', 'd'].map(name => ({ name: `${name}.png`, mimeType: 'image/png', buffer: files.swir })));
  await expect(page.locator('.persistent-error')).toContainText('Puedes subir hasta 3 imágenes');
  expect(uploads).toBe(0);
  await expect(page.getByRole('button', { name: 'Analizar imágenes', exact: true })).toBeDisabled();
});

test('incompatible result formats stop polling and show the actual submission', async ({ page }) => {
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles(resolve('../out/browser-tests/swir.png'));
  await expect(page.getByRole('button', { name: 'Analizar imágenes', exact: true })).toBeEnabled();
  const queued = { id: 'invalid-format', status: 'queued', stage: 'queued', error: null, result: null };
  jobSchema.parse(queued);
  await page.route('**/api/analyses', route => route.fulfill({ json: queued }));
  await page.route('**/api/analyses/invalid-format', route => route.fulfill({ json: { ...queued, status: 'completed', stage: 'completed', result: {} } }));
  await page.getByRole('button', { name: 'Analizar imágenes', exact: true }).click();
  await expect(page.locator('.persistent-error')).toContainText('La API ha devuelto un resultado con un formato incompatible');
  await expect(page.getByRole('button', { name: 'Volver a analizar', exact: true })).toBeEnabled();
  await page.getByText('Opciones de análisis', { exact: true }).click();
  await page.getByRole('combobox', { name: 'Resolución máxima', exact: true }).selectOption('1600');
  await page.getByText('Imágenes enviadas al análisis', { exact: true }).click();
  await expect(page.locator('.request-details')).toContainText('swir.png');
  await expect(page.locator('.request-details')).toContainText('Resolución enviada: 2500 px');
  await expect(page.locator('.request-details')).toContainText('invalid-format');
});
