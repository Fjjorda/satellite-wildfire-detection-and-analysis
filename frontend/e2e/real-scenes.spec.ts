import { expect, test } from '@playwright/test';
import { resolve } from 'node:path';
import { jobSchema, uploadSchema } from '../src/domain';
import type { Composition, Job } from '../src/domain';

const scenarios: Array<{ scene: string; types: Composition[] }> = [
  { scene: 'avila', types: ['swir', 'truecolor'] },
  { scene: 'mierla', types: ['swir', 'truecolor'] },
  { scene: 'niebla', types: ['swir'] },
  { scene: 'niebla', types: ['truecolor'] },
  { scene: 'niebla', types: ['swir', 'truecolor'] },
  { scene: 'niebla', types: ['swir', 'truecolor', 'wildfire'] },
];

test.describe('local real-scene integration', () => {
  test.skip(process.env.FIREWATCH_REAL_IMAGES !== '1', 'Uses optional local Sentinel-2 images.');

  for (const { scene, types } of scenarios) {
    for (const resolution of [1000, 1600, 2500] as const) {
      test(`${scene}: ${types.join(' + ')} at ${resolution}px`, async ({ page }) => {
        test.setTimeout(180_000);
        const uploads = new Map<string, string>();
        page.on('response', async response => {
          if (response.url().endsWith('/api/uploads') && response.ok()) {
            const image = uploadSchema.parse(await response.json());
            uploads.set(image.name, image.id);
            console.log(`${scene}/${resolution}: ${image.name}, detected=${image.detected}, size=${image.width}x${image.height}`);
          }
        });
        await page.goto('/');
        await page.getByText('Opciones de análisis', { exact: true }).click();
        await page.getByRole('combobox', { name: 'Resolución máxima', exact: true }).selectOption(String(resolution));
        await page.locator('input[type="file"]').setInputFiles(types.map(type => resolve(`../images/${scene}/${type}.jpg`)));
        for (const type of types) {
          await expect(page.getByRole('combobox', { name: `Tipo de composición de ${type}.jpg`, exact: true })).toHaveValue(type);
        }
        await expect.poll(() => uploads.size).toBe(types.length);
        await expect(page.getByRole('button', { name: 'Analizar imágenes', exact: true })).toBeEnabled();
        const submitted = page.waitForRequest(request => request.url().endsWith('/api/analyses') && request.method() === 'POST');
        const accepted = page.waitForResponse(response => response.url().endsWith('/api/analyses') && response.request().method() === 'POST');
        await page.getByRole('button', { name: 'Analizar imágenes', exact: true }).click();
        expect((await submitted).postDataJSON()).toEqual({
          ...Object.fromEntries(types.map(type => [type, uploads.get(`${type}.jpg`)])),
          max_side: resolution, smoke_recovery: true,
        });
        const response = await accepted;
        expect(response.status()).toBe(202);
        const queued = jobSchema.parse(await response.json());
        let terminal: Job = queued;
        await expect.poll(async () => {
          terminal = jobSchema.parse(await (await page.request.get(`/api/analyses/${queued.id}`)).json());
          return terminal.status;
        }, { timeout: 120_000, intervals: [500, 1000] }).toMatch(/^(completed|failed)$/);
        expect(terminal.status, `API job ${terminal.id}: ${terminal.error}`).toBe('completed');
        await expect(page.getByRole('heading', { name: 'Explora la escena' })).toBeVisible();
        await expect.poll(() => page.locator('.base-image').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
        expect(Object.keys(terminal.result!.inputs).sort()).toEqual([...types].sort());
        expect(Math.max(terminal.result!.report.width, terminal.result!.report.height)).toBeLessThanOrEqual(resolution);
        for (const type of types) expect(terminal.result!.inputs[type]?.name).toBe(`${type}.jpg`);
      });
    }
  }
});
