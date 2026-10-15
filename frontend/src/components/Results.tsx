import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from 'react-bootstrap';
import { CheckCircle2, ChevronDown, Copy, Crosshair, Info, ScanLine, ShieldCheck } from 'lucide-react';
import { defaultLayers } from '../domain';
import type { AnalysisResult, Composition, Hotspot } from '../domain';
import Viewer from './Viewer';
import ExportPanel from './ExportPanel';
import { describeReport } from '../exports';

interface Props { result: AnalysisResult; jobId: string; notify: (key: string, error?: boolean) => void }

export default function Results({ result, jobId, notify }: Props) {
  const { t, i18n } = useTranslation();
  const [base, setBase] = useState<Composition>(result.bases.truecolor ? 'truecolor' : result.bases.swir ? 'swir' : 'wildfire');
  const [visible, setVisible] = useState({ ...defaultLayers });
  const [opacity, setOpacity] = useState(1);
  const [selected, setSelected] = useState<Hotspot | null>(null);
  const report = result.report;
  const nf = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });
  const description = describeReport(report, t, nf);
  const metrics = [
    { name: 'burned', value: nf.format(report.burned_pixels), unit: t('metrics.pixelArea'), detail: t('metrics.percent', { value: nf.format(report.burned_percent) }) },
    { name: 'range', value: `${nf.format(report.strict_pixels)}–${nf.format(report.burned_pixels + report.probable_pixels)}`, unit: t('metrics.pixelArea'), detail: t('metrics.rangeHelp') },
    { name: 'fire', value: nf.format(report.hotspots.length), unit: '', detail: t('metrics.rejected', { count: report.rejected_hotspots.length }) },
    { name: 'perimeter', value: nf.format(report.perimeter_pixels), unit: 'px', detail: t('metrics.islands', { count: report.islands }) },
  ];

  function hotspotTable(hotspots: Hotspot[], rejected = false) {
    return <div className="table-scroll" role="region" aria-label={t(rejected ? 'hotspots.rejected' : 'hotspots.caption')} tabIndex={0}><table className="table hotspot-table align-middle">
      <caption className="visually-hidden">{t(rejected ? 'hotspots.rejected' : 'hotspots.caption')}</caption>
      <thead><tr>{['id', 'position', 'confidence', 'intensity', 'location', 'pixels', 'reasons'].map(key => <th scope="col" key={key}>{t(`hotspots.${key}`)}</th>)}</tr></thead>
      <tbody>{hotspots.map(hotspot => <tr key={hotspot.id} className={selected?.id === hotspot.id ? 'selected-row' : ''}>
        <th scope="row">{rejected ? `#${hotspot.id}` : <Button variant="link" className="hotspot-select" aria-label={t('hotspots.select', { id: hotspot.id })} aria-pressed={selected?.id === hotspot.id} onClick={() => setSelected(hotspot)}><Crosshair size={15} aria-hidden="true" /> #{hotspot.id}</Button>}</th>
        <td className="numeric">{hotspot.centroid_xy.map(value => nf.format(value)).join(' · ')}</td>
        <td><span className={`confidence confidence-${hotspot.confidence}`}>{hotspot.confidence === 'alta' ? '▲' : '△'} {t(`hotspots.${hotspot.confidence}`)}</span></td>
        <td>{t(`hotspots.${hotspot.intensity}`)}</td><td>{t(`hotspots.${hotspot.location}`)}</td><td className="numeric">{nf.format(hotspot.n_pixels)}</td>
        <td className="reasons-cell">{hotspot.reasons.map(reason => t(`reasons.${reason}`)).join(' · ')}</td>
      </tr>)}</tbody>
    </table></div>;
  }

  return <section className="results-area" aria-labelledby="results-title">
    <div className="result-heading"><div><p className="eyebrow">{t('result.step')}</p><h2 id="results-title">{t('result.title')}</h2></div><span className={`result-status ${report.detected ? '' : 'neutral-status'}`}><CheckCircle2 size={16} aria-hidden="true" />{t(report.detected ? 'result.detected' : 'result.notDetected')}</span></div>
    <p className="small muted result-resolution"><ScanLine size={15} aria-hidden="true" /> {t('result.resolution', report)}</p>
    <div className="surface viewer-surface"><Viewer result={result} jobId={jobId} base={base} onBase={setBase} visible={visible} onVisible={setVisible} opacity={opacity} onOpacity={setOpacity} selected={selected} /></div>
    <section className="metrics-section" aria-labelledby="metrics-title"><h3 id="metrics-title">{t('metrics.title')}</h3><dl className="metrics-grid">
      {metrics.map(metric => <div className="surface metric-card" key={metric.name}><dt>{t(`metrics.${metric.name}`)}</dt><dd className="metric-value">{metric.value} <span>{metric.unit}</span></dd><dd className="metric-detail">{metric.detail}</dd></div>)}
    </dl><p className="small muted">{t('metrics.imagePixels')} {t('metrics.water')}: <strong>{nf.format(report.water_inside_pixels)} px</strong>.</p></section>
    <section className="surface description-panel" aria-labelledby="description-title"><div className="section-heading"><h3 id="description-title"><ShieldCheck size={20} aria-hidden="true" />{t('description.title')}</h3><Button variant="outline-secondary" size="sm" onClick={async () => { try { await navigator.clipboard.writeText(description); notify('description.copied'); } catch { notify('errors.clipboard', true); } }}><Copy size={15} aria-hidden="true" /> {t('description.copy')}</Button></div><p id="result-description">{description}</p>
      {report.notes.length > 0 && <details className="analysis-notes" open><summary><Info size={16} aria-hidden="true" /> {t('notes.title')}</summary><ul>{report.notes.map(note => <li key={note}>{t(`notes.${note}`)}</li>)}</ul></details>}
    </section>
    <section className="surface hotspots-panel" aria-labelledby="hotspots-title"><details className="hotspots-disclosure"><summary><h3 id="hotspots-title"><Crosshair size={20} aria-hidden="true" />{t('hotspots.title')} <span className="count-badge">{nf.format(report.hotspots.length)}</span></h3><ChevronDown size={20} className="disclosure-chevron" aria-hidden="true" /></summary><div className="hotspots-content"><p className="small muted">{t('hotspots.help')}</p>
      <p role="status" className="visually-hidden">{selected ? t('result.selected', { id: selected.id }) : ''}</p>
      {report.hotspots.length ? hotspotTable(report.hotspots) : <p className="empty-state"><Crosshair size={22} aria-hidden="true" />{t('hotspots.empty')}</p>}
      {report.rejected_hotspots.length > 0 && <details className="mt-3"><summary>{t('hotspots.rejected')} ({nf.format(report.rejected_hotspots.length)})</summary>{hotspotTable(report.rejected_hotspots, true)}</details>}
    </div></details></section>
    <ExportPanel jobId={jobId} result={result} base={base} visible={visible} opacity={opacity} notify={notify} />
  </section>;
}
