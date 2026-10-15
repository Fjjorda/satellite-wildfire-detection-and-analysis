import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import es from './locales/es.json';
import en from './locales/en.json';

function initialLanguage(): string {
  try { return localStorage.getItem('firewatch-language') === 'en' ? 'en' : 'es'; }
  catch { return 'es'; }
}

void i18n.use(initReactI18next).init({
  resources: { es: { translation: es }, en: { translation: en } },
  lng: initialLanguage(), fallbackLng: 'es', supportedLngs: ['es', 'en'],
  interpolation: { escapeValue: false },
});
i18n.on('languageChanged', (language: string) => {
  document.documentElement.lang = language;
  document.title = `FireSentinel · ${i18n.t('app.title')}`;
  try { localStorage.setItem('firewatch-language', language); } catch { /* Language still works without storage. */ }
});
document.documentElement.lang = i18n.language;
document.title = `FireSentinel · ${i18n.t('app.title')}`;

export default i18n;
