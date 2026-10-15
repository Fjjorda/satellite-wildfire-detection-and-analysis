import type { TFunction } from 'i18next';
import { assetUrl } from './api';
import { layerColors, layerKeys } from './domain';
import type { AnalysisResult, Composition, LayerVisibility, Report } from './domain';

export function describeReport(report: Report, t: TFunction, nf: Intl.NumberFormat): string {
  const parts = [report.detected ? t('description.detected', {
    pixels: nf.format(report.burned_pixels), percent: nf.format(report.burned_percent), direction: t(`directions.${report.direction}`),
  }) : t('description.none'), t('description.fire', { count: report.hotspots.length })];
  if (report.probable_pixels) parts.push(t('description.probable', { pixels: nf.format(report.probable_pixels) }));
  if (report.water_inside_pixels || report.islands) parts.push(t('description.water'));
  return parts.join(' ');
}

export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = name;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export function hotspotCsv(report: Report, t: TFunction): string {
  const header = ['id', 'position', 'confidence', 'intensity', 'location', 'pixels', 'reasons'].map(key => t(`hotspots.${key}`));
  const rows = report.hotspots.map(hotspot => [
    hotspot.id, hotspot.centroid_xy.map(value => value.toFixed(1)).join(' / '),
    t(`hotspots.${hotspot.confidence}`), t(`hotspots.${hotspot.intensity}`),
    t(`hotspots.${hotspot.location}`), hotspot.n_pixels,
    hotspot.reasons.map(reason => t(`reasons.${reason}`)).join(' / '),
  ]);
  return '\uFEFF' + [header, ...rows].map(row => row.map(cell => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\r\n');
}

async function loadImage(url: string, signal: AbortSignal): Promise<HTMLImageElement> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error('image_download_failed');
  const objectUrl = URL.createObjectURL(await response.blob());
  try {
    const image = new Image();
    image.src = objectUrl;
    await image.decode();
    return image;
  } finally { URL.revokeObjectURL(objectUrl); }
}

export async function composeResult(jobId: string, result: AnalysisResult, base: Composition,
  visible: LayerVisibility, opacity: number, t: TFunction, signal: AbortSignal): Promise<HTMLCanvasElement> {
  const chosen = layerKeys.filter(key => visible[key]);
  const files = [result.bases[base] ?? result.bases.swir ?? result.bases.truecolor ?? result.bases.wildfire ?? '', ...chosen.map(key => result.layers[key].file)];
  const images = await Promise.all(files.map(filename => loadImage(assetUrl(jobId, filename), signal)));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(900, Math.min(result.report.width, 1800));
  const height = Math.round(canvas.width * result.report.height / result.report.width);
  const rows = Math.ceil(chosen.length / 2);
  canvas.height = height + 100 + Math.max(1, rows) * 36;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('canvas_unavailable');
  context.fillStyle = '#ffffff'; context.fillRect(0, 0, canvas.width, canvas.height);
  images.forEach((image, index) => { context.globalAlpha = index === 0 ? 1 : opacity; context.drawImage(image, 0, 0, canvas.width, height); });
  context.globalAlpha = 1;
  context.fillStyle = '#123e34'; context.font = 'bold 24px sans-serif';
  context.fillText('FireSentinel · ' + t('result.title'), 24, height + 36);
  context.fillStyle = '#343c3a'; context.font = '16px sans-serif';
  context.fillText(t('result.resolution', result.report), 24, height + 64);
  if (!chosen.length) context.fillText(t('export.noLayers'), 24, height + 100);
  chosen.forEach((key, index) => {
    const x = 24 + (index % 2) * canvas.width / 2;
    const y = height + 90 + Math.floor(index / 2) * 36;
    context.save();
    context.fillStyle = layerColors[key]; context.strokeStyle = '#000000'; context.lineWidth = 2;
    if (key === 'fire') {
      context.beginPath(); context.moveTo(x + 11, y); context.lineTo(x, y + 18); context.lineTo(x + 22, y + 18); context.closePath(); context.fill(); context.stroke();
    } else if (key === 'perimeter' || key === 'front' || key === 'smoke' || key === 'smoke_low') {
      if (key === 'smoke' || key === 'smoke_low') context.setLineDash(key === 'smoke' ? [6, 4] : [2, 4]);
      for (const [color, thickness] of [['#000000', 7], ['#FFFFFF', 5], [layerColors[key], 3]] as const) {
        context.strokeStyle = color; context.lineWidth = thickness;
        context.beginPath(); context.moveTo(x, y + 9); context.lineTo(x + 24, y + 9); context.stroke();
      }
    } else {
      context.fillRect(x, y, 22, 18); context.strokeRect(x, y, 22, 18);
      if (key === 'burned' || key === 'under_smoke') {
        context.beginPath(); context.moveTo(x, y + 14); context.lineTo(x + 14, y); context.moveTo(x + 8, y + 18); context.lineTo(x + 22, y + 4);
        if (key === 'under_smoke') { context.moveTo(x, y + 4); context.lineTo(x + 14, y + 18); context.moveTo(x + 8, y); context.lineTo(x + 22, y + 14); }
        context.stroke();
      }
    }
    context.restore();
    context.fillStyle = '#17221f'; context.font = '17px sans-serif';
    context.fillText(`${index + 1}. ${t(`layers.${key}`)}`, x + 34, y + 15);
  });
  return canvas;
}

export function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('png_failed')), 'image/png'));
}

export interface PdfPayload {
  language: string;
  title: string;
  generated: string;
  descriptionTitle: string;
  description: string;
  metricsTitle: string;
  metrics: string[];
  notesTitle: string;
  notes: string[];
  hotspotsTitle: string;
  hotspots: string[];
  noHotspots: string;
  imageTitle: string;
  imageAlt: string;
  layerCaption: string;
  footer: string;
  image: string;
  regularFont: ArrayBuffer;
  boldFont: ArrayBuffer;
}

export async function createPdf(payload: PdfPayload, signal: AbortSignal): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./pdf.worker.ts', import.meta.url), { type: 'module' });
    const finish = () => { worker.terminate(); signal.removeEventListener('abort', cancel); };
    const cancel = () => { finish(); reject(new DOMException('Aborted', 'AbortError')); };
    signal.addEventListener('abort', cancel, { once: true });
    worker.onmessage = (event: MessageEvent<{ buffer?: ArrayBuffer; error?: string }>) => {
      finish();
      if (event.data.buffer) resolve(new Blob([event.data.buffer], { type: 'application/pdf' }));
      else reject(new Error(event.data.error ?? 'pdf_failed'));
    };
    worker.onerror = event => { finish(); reject(new Error(event.message)); };
    worker.postMessage(payload, [payload.regularFont, payload.boldFont]);
  });
}
