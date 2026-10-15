import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Form } from 'react-bootstrap';
import { Expand, Layers, ZoomIn, ZoomOut } from 'lucide-react';
import { assetUrl } from '../api';
import { compositionNames, layerColors, layerKeys } from '../domain';
import type { AnalysisResult, Composition, Hotspot, LayerVisibility } from '../domain';

interface Props {
  result: AnalysisResult;
  jobId: string;
  base: Composition;
  onBase: (base: Composition) => void;
  visible: LayerVisibility;
  onVisible: (visible: LayerVisibility) => void;
  opacity: number;
  onOpacity: (value: number) => void;
  selected: Hotspot | null;
}

export default function Viewer({ result, jobId, base, onBase, visible, onVisible, opacity, onOpacity, selected }: Props) {
  const { t, i18n } = useTranslation();
  const [zoom, setZoom] = useState(1);
  const [compare, setCompare] = useState(false);
  const [divider, setDivider] = useState(50);
  const [simulation, setSimulation] = useState('normal');
  const [imageError, setImageError] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const report = result.report;
  const nf = new Intl.NumberFormat(i18n.language);

  useEffect(() => {
    if (!selected || !viewport.current || !image.current) return;
    const scale = image.current.clientWidth / report.width;
    viewport.current.scrollTo({ left: selected.centroid_xy[0] * scale - viewport.current.clientWidth / 2, top: selected.centroid_xy[1] * scale - viewport.current.clientHeight / 2 });
    viewport.current.scrollIntoView({ block: 'nearest' });
  }, [selected, report.width]);

  return <div className="viewer-layout">
    <div className="viewer-main">
      <div className="viewer-toolbar">
        <Form.Group controlId="base-image" className="base-select"><Form.Label>{t('result.base')}</Form.Label><Form.Select size="sm" value={base} onChange={event => { const value = event.target.value; if (value === 'swir' || value === 'truecolor' || value === 'wildfire') onBase(value); }}>
          {Object.keys(compositionNames).filter(key => key in result.bases).map(key => <option key={key} value={key}>{compositionNames[key as Composition]}</option>)}
        </Form.Select></Form.Group>
        <div className="zoom-controls" role="group" aria-label={t('result.zoom')}>
          <Button variant="outline-secondary" className="icon-button" aria-label={t('result.zoomOut')} disabled={zoom <= 1} onClick={() => setZoom(value => Math.max(1, value - 0.5))}><ZoomOut size={18} aria-hidden="true" /></Button>
          <output aria-label={t('result.zoom')}>{nf.format(zoom * 100)}%</output>
          <Button variant="outline-secondary" className="icon-button" aria-label={t('result.zoomIn')} disabled={zoom >= 4} onClick={() => setZoom(value => Math.min(4, value + 0.5))}><ZoomIn size={18} aria-hidden="true" /></Button>
          <Button variant="outline-secondary" className="icon-button" aria-label={t('result.fit')} onClick={() => { setZoom(1); viewport.current?.scrollTo(0, 0); }}><Expand size={18} aria-hidden="true" /></Button>
        </div>
      </div>
      <svg className="filter-definitions" aria-hidden="true" focusable="false"><defs>
        <filter id="protanopia"><feColorMatrix type="matrix" values=".567 .433 0 0 0 .558 .442 0 0 0 0 .242 .758 0 0 0 0 0 1 0" /></filter>
        <filter id="deuteranopia"><feColorMatrix type="matrix" values=".625 .375 0 0 0 .7 .3 0 0 0 0 .3 .7 0 0 0 0 0 1 0" /></filter>
        <filter id="tritanopia"><feColorMatrix type="matrix" values=".95 .05 0 0 0 0 .433 .567 0 0 0 .475 .525 0 0 0 0 0 1 0" /></filter>
      </defs></svg>
      <figure className="image-figure">
        <div ref={viewport} className="image-viewport" tabIndex={0} role="region" aria-label={t('result.viewer')} aria-describedby="viewer-help result-description">
          <div className="image-stack" style={{ width: `${zoom * 100}%`, filter: simulation === 'normal' ? undefined : `url(#${simulation})` }}>
            <img ref={image} className="base-image" src={assetUrl(jobId, result.bases[base] ?? result.bases.swir ?? result.bases.truecolor ?? result.bases.wildfire ?? '')} width={report.width} height={report.height}
              alt={t('description.alt', { pixels: nf.format(report.burned_pixels), count: report.hotspots.length })} onError={() => setImageError(true)} />
            <div className="overlay-stack" style={{ opacity, clipPath: compare ? `inset(0 0 0 ${divider}%)` : undefined }}>
              {layerKeys.filter(key => visible[key]).map(key => <img key={key} className="image-layer" src={assetUrl(jobId, result.layers[key].file)} alt="" aria-hidden="true" onError={() => setImageError(true)} />)}
            </div>
            {selected && <svg className="selection-overlay" viewBox={`0 0 ${report.width} ${report.height}`} aria-hidden="true"><circle cx={selected.centroid_xy[0]} cy={selected.centroid_xy[1]} r={Math.max(16, report.width / 45)} fill="none" stroke="#fff" strokeWidth={6} /><circle cx={selected.centroid_xy[0]} cy={selected.centroid_xy[1]} r={Math.max(16, report.width / 45)} fill="none" stroke="#000" strokeWidth={3} strokeDasharray="5 4" /></svg>}
            {compare && <><div className="compare-line" style={{ left: `${divider}%` }} /><span className="image-label original-label">{t('result.original')}</span><span className="image-label result-label">{t('result.processed')}</span></>}
          </div>
        </div>
        <figcaption className="small muted" id="viewer-help">{t('result.scrollHelp')}</figcaption>
      </figure>
      {imageError && <p className="alert alert-danger" role="alert">{t('result.previewError')}</p>}
      <div className="comparison-controls"><Form.Check type="switch" role="switch" id="compare" label={t('result.compare')} checked={compare} onChange={event => setCompare(event.target.checked)} />
        {compare && <Form.Group controlId="comparison-position" className="mt-2"><Form.Label className="small">{t('result.divider')}: {nf.format(divider)}%</Form.Label><Form.Range min={0} max={100} value={divider} onChange={event => setDivider(Number(event.target.value))} /></Form.Group>}
      </div>
    </div>
    <aside className="layer-panel" aria-labelledby="layers-title">
      <h3 id="layers-title"><Layers size={18} aria-hidden="true" /> {t('result.layers')}</h3>
      <div className="layer-shortcuts"><button type="button" onClick={() => onVisible(Object.fromEntries(layerKeys.map(key => [key, true])) as LayerVisibility)}>{t('result.showAll')}</button><button type="button" onClick={() => onVisible(Object.fromEntries(layerKeys.map(key => [key, false])) as LayerVisibility)}>{t('result.hideAll')}</button></div>
      <fieldset><legend className="visually-hidden">{t('result.layers')}</legend>
        {layerKeys.map(key => <div className="layer-control" key={key}>
          <Form.Check id={`layer-${key}`} checked={visible[key]} onChange={event => onVisible({ ...visible, [key]: event.target.checked })}
            label={<><span className={`layer-swatch swatch-${key}`} style={{ '--layer-color': layerColors[key] } as React.CSSProperties} aria-hidden="true" />{t(`layers.${key}`)}</>} />
          {result.layers[key].count === 0 && <small className="muted empty-layer">{t('result.emptyLayer')}</small>}
        </div>)}
      </fieldset>
      <p className="small muted front-note">{t('result.frontHelp')}</p>
      <Form.Group controlId="layer-opacity"><Form.Label className="small">{t('result.opacity')}: {nf.format(Math.round(opacity * 100))}%</Form.Label><Form.Range min={10} max={100} value={opacity * 100} onChange={event => onOpacity(Number(event.target.value) / 100)} /></Form.Group>
      <Form.Group controlId="simulation" className="mt-3"><Form.Label className="small">{t('result.simulation')}</Form.Label><Form.Select size="sm" value={simulation} aria-describedby="simulation-help" onChange={event => setSimulation(event.target.value)}>
        {['normal', 'protanopia', 'deuteranopia', 'tritanopia'].map(value => <option key={value} value={value}>{t(`result.${value}`)}</option>)}
      </Form.Select><Form.Text id="simulation-help">{t('result.simulationHelp')}</Form.Text></Form.Group>
    </aside>
  </div>;
}
