import { createI18n } from 'vue-i18n';
import en from '../locales/en.json';

// I18N-01..I18N-03: English ('en') is the only shipped locale and fallback.
// In development mode, 'pseudo-dev' can be loaded to prove component independence.
export const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: {
    en,
  },
});
