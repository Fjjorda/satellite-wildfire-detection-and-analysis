import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Form } from 'react-bootstrap';
import { Archive, Download, FileJson, FileText, ImageDown, Languages, LoaderCircle, Sheet } from 'lucide-react';
import { layerKeys } from '../domain';
import type { AnalysisResult, Composition, LayerVisibility } from '../domain';
import { fetchResource, generateExport } from '../export-resources';
import type { ExportFormat, ExportLanguage } from '../export-resources';
import { downloadBlob } from '../exports';

interface Props {
  jobId: string; result: AnalysisResult; base: Composition; visible: LayerVisibility;
  opacity: number; notify: (key: string, error?: boolean) => void;
}

export default function ExportPanel({ jobId, result, base, visible, opacity, notify }: Props) {
  const { t, i18n } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [chosenLanguage, setChosenLanguage] = useState<ExportLanguage | null>(null);
  const language = chosenLanguage ?? (i18n.resolvedLanguage === 'en' ? 'en' : 'es');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => { controller.current?.abort(); controller.current = null; }, []);

  async function save(format: ExportFormat, mask?: string) {
    if (busy) return;
    const request = new AbortController(); controller.current = request;
    setBusy(true);
    try {
      const blob = mask
        ? await fetchResource(jobId, mask, request.signal)
        : await generateExport({ jobId, result, base, visible, opacity, language }, format, request.signal);
      if (!request.signal.aborted) {
        const stem = mask ? mask.replace('.png', '') : format === 'zip' ? 'resources' : 'result';
        downloadBlob(blob, `firesentinel-${stem}-${language}.${format}`);
        notify('export.ready');
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        request.abort();
        console.error(error);
        notify('errors.export_failed', true);
      }
    } finally {
      if (controller.current === request) { controller.current = null; setBusy(false); }
    }
  }

  const exports = [
    { key: 'png', label: 'png', icon: ImageDown }, { key: 'pdf', label: 'pdf', icon: FileText },
    { key: 'json', label: 'json', icon: FileJson }, { key: 'csv', label: 'csv', icon: Sheet },
    { key: 'txt', label: 'text', icon: FileText },
  ] as const;

  return <section className="surface export-panel" aria-labelledby="export-title">
    <div className="section-heading export-heading">
      <h3 id="export-title"><Download size={21} aria-hidden="true" />{t('export.title')}</h3>
      <Form.Group controlId="export-language" className="export-language">
        <Form.Label><Languages size={15} aria-hidden="true" />{t('export.language')}</Form.Label>
        <Form.Select size="sm" value={language} disabled={busy} onChange={event => {
          if (event.target.value === 'es' || event.target.value === 'en') setChosenLanguage(event.target.value);
        }}><option value="es">Español</option><option value="en">English</option></Form.Select>
      </Form.Group>
    </div>
    <p className="small muted">{t('export.help')}</p>
    <div className="archive-download">
      <Button variant="primary" disabled={busy} aria-describedby="archive-help" onClick={() => { void save('zip'); }}>
        <Archive size={19} aria-hidden="true" />{t('export.zip')}
      </Button>
      <p id="archive-help" className="small muted">{t('export.zipHelp')}</p>
    </div>
    <div className="export-buttons">{exports.map(({ key, label, icon: Icon }) => <Button key={key} variant="outline-secondary" disabled={busy} onClick={() => { void save(key); }}><Icon size={17} aria-hidden="true" />{t(`export.${label}`)}</Button>)}</div>
    <p role="status" aria-live="polite" className="small export-status">{busy && <><LoaderCircle size={15} className="spin" aria-hidden="true" /> {t('export.busy')}</>}</p>
    <details className="mask-details"><summary>{t('export.masks')}</summary>
      <div id="masks-help" className="mask-explanation small muted">
        <p>{t('export.masksHelp')}</p><p>{t('export.masksValues')}</p><p>{t('export.masksResolution')}</p>
      </div>
      <div className="mask-downloads">{layerKeys.filter(key => result.files.includes(`mask-${key}.png`)).map(key =>
        <Button variant="link" disabled={busy} key={key} aria-describedby="masks-help" onClick={() => { void save('png', `mask-${key}.png`); }}>
          <Download size={14} aria-hidden="true" />{t('export.mask', { name: t(`layers.${key}`) })}
        </Button>,
      )}</div>
    </details>
  </section>;
}
