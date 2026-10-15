import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Form, Spinner } from 'react-bootstrap';
import { Check, FileImage, ScanLine, Tag, Trash2, UploadCloud } from 'lucide-react';
import { apiUrl } from '../api';
import { compositionNames, MAX_INPUT_IMAGES } from '../domain';
import type { AnalysisOptions, Composition, UploadedImage } from '../domain';

interface Props {
  images: UploadedImage[];
  uploading: boolean;
  running: boolean;
  options: AnalysisOptions;
  onFiles: (files: File[]) => void;
  onAssign: (id: string, assignment: Composition | 'unused') => void;
  onRemove: (id: string) => void;
  onOptions: (options: AnalysisOptions) => void;
}

export default function UploadPanel({ images, uploading, running, options, onFiles, onAssign, onRemove, onOptions }: Props) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const disabled = uploading || running;
  const reference = images.find(image => image.assignment === 'swir')
    ?? images.find(image => image.assignment === 'truecolor')
    ?? images.find(image => image.assignment !== 'unused');
  const mismatched = reference && images.some(image => image.assignment !== 'unused' && Math.abs(image.width / image.height - reference.width / reference.height) / (reference.width / reference.height) > 0.02);
  return <section className="surface preparation" aria-labelledby="upload-title">
    <div className="section-heading"><div><p className="eyebrow">{t('upload.step')}</p><h2 id="upload-title">{t('upload.title')}</h2></div><span className="section-icon"><FileImage aria-hidden="true" size={23} /></span></div>
    <p className="muted">{t('upload.help')}</p>
    <div className={`drop-zone ${dragging ? 'is-dragging' : ''} ${disabled ? 'is-disabled' : ''}`}
      onDragOver={event => { event.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={event => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
      onDrop={event => { event.preventDefault(); setDragging(false); if (!disabled) onFiles(Array.from(event.dataTransfer.files)); }}>
      <UploadCloud size={34} strokeWidth={1.5} aria-hidden="true" />
      <p className="drop-title">{t('upload.drop')}</p><p className="muted small">{t('upload.or')}</p>
      <input ref={input} type="file" multiple accept="image/png,image/jpeg,.png,.jpg,.jpeg" hidden
        aria-label={t('upload.choose')} disabled={disabled}
        onChange={event => { onFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
      <Button variant="outline-primary" disabled={disabled || images.length >= MAX_INPUT_IMAGES} aria-describedby="upload-limits" onClick={() => input.current?.click()}>
        {uploading ? <Spinner as="span" size="sm" aria-hidden="true" /> : <span aria-hidden="true">＋</span>} {t('upload.choose')}
      </Button>
      <small className="muted d-block mt-3" id="upload-limits">{t('upload.formats')}<br />{t('upload.limit', { max: MAX_INPUT_IMAGES })}</small>
    </div>
    <p className="small muted mt-3">{t('upload.guessHelp')}</p>
    {uploading && <p role="status" className="small"><Spinner as="span" size="sm" aria-hidden="true" /> {t('upload.busy')}</p>}
    {images.length > 0 && <ul className="upload-list list-unstyled">
      {images.map(image => {
        const duplicate = image.assignment !== 'unused' && images.some(other => other.id !== image.id && other.assignment === image.assignment);
        const SourceIcon = image.assignmentSource === 'filename' ? Tag : image.assignmentSource === 'manual' ? Check : ScanLine;
        return <li key={image.id} className="upload-item">
        <img src={apiUrl(image.preview)} alt="" className="upload-preview" />
        <div className="upload-detail"><span className="file-name" title={image.name}>{image.name}</span>
          <span className="small muted">{t('upload.dimensions', image)}</span>
          <span className="small detected"><SourceIcon size={13} aria-hidden="true" /> {t(`upload.source_${image.assignmentSource}`, { kind: image.assignment === 'unused' ? t('upload.unused') : compositionNames[image.assignment] })}</span>
          <Form.Select size="sm" aria-label={t('upload.kind', { name: image.name })} value={image.assignment} disabled={disabled}
            isInvalid={duplicate || image.assignment === 'unused'} aria-invalid={duplicate || image.assignment === 'unused'} aria-describedby="analysis-help"
            onChange={event => { const value = event.target.value; if (value === 'swir' || value === 'truecolor' || value === 'wildfire' || value === 'unused') onAssign(image.id, value); }}>
            {Object.entries(compositionNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
            <option value="unused">{t('upload.unused')}</option>
          </Form.Select>
        </div>
        <Button variant="link" className="icon-button remove-button" aria-label={t('upload.remove', { name: image.name })} disabled={disabled} onClick={() => onRemove(image.id)}><Trash2 size={18} aria-hidden="true" /></Button>
      </li>; })}
    </ul>}
    {mismatched && <p className="inline-note warning-note">{t('upload.mismatch')}</p>}
    <div className="composition-guide">
      <p><strong>SWIR</strong><span className="tiny-badge">{t('upload.recommended')}</span><small>{t('upload.swirHelp')}</small></p>
      <p><strong>True Color</strong><span className="tiny-badge">{t('upload.recommended')}</span><small>{t('upload.truecolorHelp')}</small></p>
      <p><strong>Wildfire</strong><span className="tiny-badge neutral">{t('upload.optional')}</span><small>{t('upload.wildfireHelp')}</small></p>
    </div>
    <details className="settings-panel"><summary>{t('settings.title')}</summary>
      <fieldset disabled={disabled} className="mt-3">
        <Form.Group controlId="resolution" className="mb-3"><Form.Label>{t('settings.resolution')}</Form.Label>
          <Form.Select value={options.max_side} aria-describedby="resolution-help" onChange={event => { const value = Number(event.target.value); if (value === 1000 || value === 1600 || value === 2500) onOptions({ ...options, max_side: value }); }}>
            {[1000, 1600, 2500].map(value => <option key={value} value={value}>{value} px</option>)}
          </Form.Select><Form.Text id="resolution-help">{t('settings.resolutionHelp')}</Form.Text>
        </Form.Group>
      </fieldset>
    </details>
  </section>;
}
