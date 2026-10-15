import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import i18n from '../i18n';

export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError(): { failed: boolean } { return { failed: true }; }
  override componentDidCatch(error: Error, info: ErrorInfo): void { console.error(error, info.componentStack); }
  override render(): ReactNode {
    if (this.state.failed) return <main className="container py-5"><h1>FireSentinel</h1><p role="alert">{i18n.t('errors.unknown')}</p><button className="btn btn-primary" onClick={() => location.reload()}>{i18n.t('progress.retry')}</button></main>;
    return this.props.children;
  }
}
