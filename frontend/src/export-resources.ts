import regularFontUrl from '@fontsource/noto-sans/files/noto-sans-latin-400-normal.woff?url';
import boldFontUrl from '@fontsource/noto-sans/files/noto-sans-latin-700-normal.woff?url';
import { assetUrl } from './api';
import { createArchive } from './archive';
import { compositionNames, layerKeys } from './domain';
import type { AnalysisResult, Composition, LayerVisibility } from './domain';
import { canvasBlob, composeResult, createPdf, describeReport, hotspotCsv } from './exports';
import i18n from './i18n';

export type ExportLanguage = 'es' | 'en';
export type ExportFormat = 'png' | 'pdf' | 'json' | 'csv' | 'txt' | 'zip';
export interface ExportSettings {
  jobId: string;
  result: AnalysisResult;
  base: Composition;
  visible: LayerVisibility;
  opacity: number;
  language: ExportLanguage;
}

/**
 * Reads an available analysis asset and rejects incomplete downloads.
 * @throws If the request fails, the file is missing, or the request is aborted.
 */
export async function fetchResource(jobId: string, file: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(assetUrl(jobId, file), { signal });
  if (!response.ok) throw new Error('resource_download_failed');
  return response.blob();
}

/**
 * Freezes the output language and includes hidden resources in complete ZIP exports.
 * @throws If any required resource, image, font, or export cannot be generated.
 */
export async function generateExport(settings: ExportSettings, format: ExportFormat, signal: AbortSignal): Promise<Blob> {
  const { jobId, result, base, visible, opacity, language } = settings;
  const t = i18n.getFixedT(language);
  const nf = new Intl.NumberFormat(language, { maximumFractionDigits: 1 });
  const report = result.report;
  const description = describeReport(report, t, nf);
  const notes = report.notes.map(note => t(`notes.${note}`));
  const json = new Blob([JSON.stringify({
    ...report, options: result.options, language, description, notes_text: notes,
  }, null, 2)], { type: 'application/json' });
  const text = new Blob([description + '\n\n' + notes.join('\n')], { type: 'text/plain;charset=utf-8' });
  const csv = new Blob([hotspotCsv(report, t)], { type: 'text/csv;charset=utf-8' });
  if (format === 'json') return json;
  if (format === 'txt') return text;
  if (format === 'csv') return csv;

  const canvas = await composeResult(jobId, result, base, visible, opacity, t, signal);
  const png = await canvasBlob(canvas);
  if (format === 'png') return png;
  const fonts = await Promise.all([regularFontUrl, boldFontUrl].map(async url => {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error('font_download_failed');
    return response.arrayBuffer();
  }));
  const layerCaption = `${t('export.visibleLayers')}: ${layerKeys.filter(key => visible[key]).map(key => t(`layers.${key}`)).join(', ') || t('export.noLayers')}.`;
  const pdf = await createPdf({
    language, title: t('export.pdfTitle'),
    generated: t('export.date', { date: new Intl.DateTimeFormat(language, { dateStyle: 'long', timeStyle: 'short' }).format(new Date()) }),
    descriptionTitle: t('description.title'), description, metricsTitle: t('metrics.title'),
    metrics: [
      `${t('metrics.burned')}: ${nf.format(report.burned_pixels)} px (${nf.format(report.burned_percent)}%).`,
      `${t('metrics.range')}: ${nf.format(report.strict_pixels)}–${nf.format(report.burned_pixels + report.probable_pixels)} px.`,
      `${t('metrics.perimeter')}: ${nf.format(report.perimeter_pixels)} px.`,
      `${t('metrics.water')}: ${nf.format(report.water_inside_pixels)} px.`,
      t('metrics.islands', { count: report.islands }), t('result.resolution', report),
    ],
    notesTitle: t('notes.title'), notes,
    hotspotsTitle: t('hotspots.title'), noHotspots: t('hotspots.empty'),
    hotspots: report.hotspots.map(hotspot => [
      `#${hotspot.id}`, `${t('hotspots.position')}: ${hotspot.centroid_xy.map(value => nf.format(value)).join(' / ')}`,
      `${t('hotspots.confidence')}: ${t(`hotspots.${hotspot.confidence}`)}`,
      `${t('hotspots.intensity')}: ${t(`hotspots.${hotspot.intensity}`)}`,
      t(`hotspots.${hotspot.location}`), `${nf.format(hotspot.n_pixels)} px`,
      hotspot.reasons.map(reason => t(`reasons.${reason}`)).join(', '),
    ].join(' · ')),
    imageTitle: t('export.pdfImage'), imageAlt: description, layerCaption,
    footer: t('export.resolutionNote'), image: canvas.toDataURL('image/png'),
    regularFont: fonts[0]!, boldFont: fonts[1]!,
  }, signal);
  if (format === 'pdf') return pdf;

  const prefix = `firesentinel-result-${language}`;
  const files: Record<string, Blob> = {
    [`${prefix}.png`]: png, [`${prefix}.pdf`]: pdf, [`${prefix}.json`]: json,
    [`${prefix}.csv`]: csv, [`${prefix}.txt`]: text,
    [`README-${language}.txt`]: new Blob([[
      'FireSentinel', t('export.zipHelp'), t('result.resolution', report),
      `${t('result.base')}: ${compositionNames[base]}`, layerCaption,
      t('export.masks'), t('export.masksHelp'), t('export.masksValues'),
      t('export.masksResolution'), t('export.resolutionNote'),
    ].join('\n\n')], { type: 'text/plain;charset=utf-8' }),
  };
  // Bound concurrent downloads while retaining every asset, including hidden layers.
  for (let offset = 0; offset < result.files.length; offset += 4) {
    await Promise.all(result.files.slice(offset, offset + 4).map(async file => {
      if (!/^[\w-]+\.[\w]+$/.test(file)) throw new Error('invalid_resource_name');
      const folder = file.startsWith('mask-') ? 'masks' : file.startsWith('base-') ? 'compositions' : file.startsWith('layer-') ? 'layers' : 'analysis';
      files[`${folder}/${file}`] = await fetchResource(jobId, file, signal);
    }));
  }
  return createArchive(files, signal);
}
