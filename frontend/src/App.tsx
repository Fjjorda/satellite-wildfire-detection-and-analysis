import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Form, Spinner, Toast, ToastContainer } from 'react-bootstrap';
import { ArrowDownToLine, ArrowRight, Check, CheckCircle2, CircleAlert, Flame, ImagePlus, Languages, Paintbrush, ScanLine, Sparkles } from 'lucide-react';
import { ApiError, getAnalysis, startAnalysis, uploadImage } from './api';
import { assignmentsValid, compositionNames, MAX_INPUT_IMAGES, stages } from './domain';
import type { AnalysisOptions, Job, UploadedImage } from './domain';
import UploadPanel from './components/UploadPanel';
import Results from './components/Results';
import { assignUploadedImage } from './input-images';

type Theme = 'light' | 'dark' | 'system' | 'contrast';
interface Notice { id: number; key: string; error: boolean }
function initialTheme(): Theme {
  try { const stored = localStorage.getItem('firewatch-theme'); if (stored === 'light' || stored === 'dark' || stored === 'contrast') return stored; }
  catch { /* System mode works without browser storage. */ }
  return 'system';
}

export default function App() {
  const { t, i18n } = useTranslation();
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [images, setImages] = useState<UploadedImage[]>([]);
  const [options, setOptions] = useState<AnalysisOptions>({ max_side: 2500 });
  const [uploading, setUploading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [submittedImages, setSubmittedImages] = useState<UploadedImage[]>([]);
  const [submittedOptions, setSubmittedOptions] = useState<AnalysisOptions>({ max_side: 2500 });
  const [paused, setPaused] = useState(false);
  const uploadController = useRef<AbortController | null>(null);
  const startController = useRef<AbortController | null>(null);
  const running = starting || job?.status === 'queued' || job?.status === 'running';
  const valid = assignmentsValid(images);
  const jobId = job?.id;
  const jobStatus = job?.status;

  function notify(key: string, isError = false) { setNotice({ id: Date.now(), key, error: isError }); setPaused(false); }
  function fail(cause: unknown) {
    if (cause instanceof DOMException && cause.name === 'AbortError') return;
    const key = `errors.${cause instanceof ApiError ? cause.code : 'unknown'}`;
    setError(i18n.exists(key) ? key : 'errors.unknown');
    notify(i18n.exists(key) ? key : 'errors.unknown', true);
  }

  useEffect(() => {
    const preference = matchMedia('(prefers-color-scheme: dark)');
    function apply() {
      document.documentElement.dataset.bsTheme = theme === 'system' ? (preference.matches ? 'dark' : 'light') : theme === 'contrast' ? 'dark' : theme;
      document.documentElement.dataset.contrast = String(theme === 'contrast');
    }
    apply();
    try { localStorage.setItem('firewatch-theme', theme); } catch { /* The active theme still applies. */ }
    preference.addEventListener('change', apply);
    return () => preference.removeEventListener('change', apply);
  }, [theme]);

  useEffect(() => {
    if (!notice || notice.error || paused) return;
    const timeout = setTimeout(() => setNotice(null), 8000);
    return () => clearTimeout(timeout);
  }, [notice, paused]);

  useEffect(() => () => { uploadController.current?.abort(); startController.current?.abort(); }, []);

  useEffect(() => {
    if (!jobId || (jobStatus !== 'queued' && jobStatus !== 'running')) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await getAnalysis(jobId!, controller.signal);
        if (controller.signal.aborted) return;
        setJob(next); setPollError(false);
        if (next.status === 'completed') { setNotice({ id: Date.now(), key: 'result.ready', error: false }); return; }
        if (next.status === 'failed') { setError(`errors.${next.error ?? 'analysis_failed'}`); return; }
        timer = setTimeout(() => { void poll(); }, 800);
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (cause instanceof ApiError && cause.code === 'job_not_found') { setError('errors.job_not_found'); setJob(null); }
          else if (cause instanceof ApiError && cause.code === 'invalid_response') {
            setError('errors.invalid_response');
            setJob(current => current ? { ...current, status: 'failed', stage: 'failed', error: 'invalid_response' } : null);
            setPollError(false);
          }
          else setPollError(true);
        }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [jobId, jobStatus, retry]);

  async function addFiles(files: File[]) {
    if (!files.length || running || uploading) return;
    if (images.length + files.length > MAX_INPUT_IMAGES) { fail(new ApiError('too_many_images')); return; }
    const controller = new AbortController();
    uploadController.current = controller;
    setUploading(true); setError(null);
    let failed = false;
    for (const file of files) {
      try {
        const uploaded = await uploadImage(file, controller.signal);
        setImages(current => [...current, assignUploadedImage(uploaded)]);
      } catch (cause) { failed = true; fail(cause); if (controller.signal.aborted) break; }
    }
    setUploading(false);
    if (!failed) notify('upload.added');
  }

  async function analyze() {
    if (!valid || running || uploading) return;
    const controller = new AbortController(); startController.current = controller;
    setStarting(true); setError(null); setPollError(false);
    setSubmittedImages(images.map(image => ({ ...image })));
    setSubmittedOptions({ ...options });
    try { setJob(await startAnalysis(images, options, controller.signal)); }
    catch (cause) { fail(cause); }
    finally { setStarting(false); }
  }

  const stageIndex = Math.max(0, stages.findIndex(stage => stage === job?.stage));
  const noImages = !images.some(image => image.assignment === 'swir' || image.assignment === 'truecolor');
  const needsType = images.some(image => image.assignment === 'unused');
  return <>
    <a className="skip-link" href="#main">{t('app.skip')}</a>
    <header className="app-header"><div className="app-container header-inner">
      <a href="#main" className="brand" aria-label="FireSentinel"><span className="brand-icon"><Flame size={25} strokeWidth={1.8} aria-hidden="true" /></span><span>FireSentinel</span></a>
      <span className="header-subtitle">{t('app.subtitle')}</span>
      <div className="header-controls">
        <div className="header-select"><Languages size={16} aria-hidden="true" /><Form.Select size="sm" aria-label={t('app.language')} value={i18n.language} onChange={event => { void i18n.changeLanguage(event.target.value); }}><option value="es">Español</option><option value="en">English</option></Form.Select></div>
        <div className="header-select"><Paintbrush size={16} aria-hidden="true" /><Form.Select size="sm" aria-label={t('app.theme')} value={theme} onChange={event => { const value = event.target.value; if (value === 'system' || value === 'light' || value === 'dark' || value === 'contrast') setTheme(value); }}>{(['system', 'light', 'dark', 'contrast'] as const).map(value => <option value={value} key={value}>{t(`app.${value}`)}</option>)}</Form.Select></div>
      </div>
    </div></header>
    <main id="main" tabIndex={-1} className="app-container">
      <section className="hero" aria-labelledby="page-title"><div className="hero-copy"><h1 id="page-title">{t('intro.title')}</h1><p>{t('intro.body')}</p></div><div className="hero-mark" aria-hidden="true"><ScanLine size={116} strokeWidth={0.65} /><span className="hero-cross">＋</span></div></section>
      <ol className="workflow" aria-label={t('app.title')}><li className={images.length ? 'complete' : 'current'}><span>{images.length ? <Check size={16} aria-hidden="true" /> : '01'}</span><ImagePlus size={17} aria-hidden="true" />{t('intro.step1')}</li><li className={running ? 'current' : job?.status === 'completed' ? 'complete' : ''}><span>02</span><Sparkles size={17} aria-hidden="true" />{t('intro.step2')}</li><li className={job?.status === 'completed' ? 'current' : ''}><span>03</span><ArrowDownToLine size={17} aria-hidden="true" />{t('intro.step3')}</li></ol>
      <div className={`workspace ${job?.status === 'completed' ? 'has-results' : ''}`}>
        <div className="preparation-column"><UploadPanel images={images} uploading={uploading} running={running} options={options} onFiles={files => { void addFiles(files); }} onAssign={(id, assignment) => setImages(current => current.map(image => image.id === id ? { ...image, assignment, assignmentSource: 'manual' } : image))} onRemove={id => setImages(current => current.filter(image => image.id !== id))} onOptions={setOptions} />
          <div className="analyze-action"><Button size="lg" className="analyze-button" disabled={!valid || running || uploading} aria-describedby="analysis-help" onClick={() => { void analyze(); }}>{running ? <Spinner as="span" size="sm" aria-hidden="true" /> : <Sparkles size={18} aria-hidden="true" />}{t(running ? 'settings.running' : job ? 'settings.again' : 'settings.analyze')}<ArrowRight size={19} aria-hidden="true" /></Button><p id="analysis-help" className={`small ${!valid && images.length ? 'validation-note' : 'muted'}`}>{!valid ? t(needsType ? 'upload.needsType' : noImages ? 'upload.missing' : 'upload.duplicate') : t('settings.imageOnly')}</p></div>
          {error && <div className="alert alert-danger persistent-error"><strong>{t('errors.heading')}</strong><p className="mb-0 mt-1">{t(error)}</p>
            {submittedImages.length > 0 && job?.status === 'failed' && (error === 'errors.analysis_failed' || error === 'errors.invalid_response') && <details className="request-details"><summary>{t('diagnostics.title')}</summary><dl>
              {submittedImages.map(image => <div key={image.id}><dt>{image.assignment === 'unused' ? t('upload.unused') : compositionNames[image.assignment]}</dt><dd>{image.name}</dd></div>)}
            </dl><p className="small">{t('diagnostics.resolution', { value: submittedOptions.max_side })}</p><p className="small">{t('diagnostics.job', { id: job.id })}</p></details>}
          </div>}
        </div>
        {running ? <section className="surface progress-panel" aria-labelledby="progress-title"><div className="progress-emblem"><ScanLine size={45} strokeWidth={1.2} aria-hidden="true" /></div><p className="eyebrow">FIRESENTINEL / SENTINEL-2</p><h2 id="progress-title">{t('progress.title')}</h2><p role="status" aria-live="polite" aria-atomic="true">{t(`progress.${job?.stage ?? 'queued'}`)}</p><progress max={stages.length} value={stageIndex + 1} aria-label={t('progress.title')} aria-valuetext={t(`progress.${job?.stage ?? 'queued'}`)} /><p className="small muted">{t('progress.help')}</p>{pollError && <div role="alert" className="alert alert-warning"><p>{t('progress.reconnect')}</p><Button variant="outline-primary" onClick={() => { setPollError(false); setRetry(value => value + 1); }}>{t('progress.retry')}</Button></div>}</section>
          : job?.status === 'completed' && job.result ? <Results key={job.id} jobId={job.id} result={job.result} notify={notify} />
          : <section className="empty-workspace" aria-label={t('intro.step2')}><div className="empty-image-grid" aria-hidden="true"><ScanLine size={72} strokeWidth={0.8} /><span className="grid-corner corner-a" /><span className="grid-corner corner-b" /></div><p className="eyebrow">PNG / JPG</p><h2>{t('intro.step2')}</h2><p className="muted">{t('upload.help')}</p><div className="empty-tags"><span>SWIR <span aria-hidden="true">→</span> {t('layers.burned')}</span><span>True Color <span aria-hidden="true">→</span> {t('layers.smoke')}</span></div></section>}
      </div>
    </main>
    <footer className="app-container app-footer"><span><Flame size={16} aria-hidden="true" /> FireSentinel · {t('app.footer')}</span><p>{t('app.footerNote')}</p></footer>
    <div className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{notice && !notice.error ? t(notice.key) : ''}</div>
    <div className="visually-hidden" role="alert" aria-atomic="true">{notice?.error ? t(notice.key) : ''}</div>
    <ToastContainer position="bottom-end" className="notification-container"><Toast key={notice?.id} show={!!notice} onClose={() => setNotice(null)} role="group" aria-label={t('notification.title')} aria-live="off" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocus={() => setPaused(true)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false); }}>
      <Toast.Body><span className={`notification-icon ${notice?.error ? 'is-error' : ''}`}>{notice?.error ? <CircleAlert size={21} aria-hidden="true" /> : <CheckCircle2 size={21} aria-hidden="true" />}</span><span>{notice ? t(notice.key) : ''}</span><button className="notification-close" aria-label={t('errors.close')} onClick={() => setNotice(null)}>×</button></Toast.Body>
    </Toast></ToastContainer>
  </>;
}
