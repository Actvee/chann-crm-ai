"use client";

import { LOCALES, type Locale } from "./index";
import { useLanguage } from "./LanguageProvider";

const LABELS: Record<Locale, string> = { th: "ไทย", en: "English" };

export function LanguageSwitcher() {
  const { locale, t, setLocale } = useLanguage();

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <label htmlFor="chann-locale">{t.common.language}</label>
      <select
        id="chann-locale"
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
      >
        {LOCALES.map((code) => (
          <option key={code} value={code}>
            {LABELS[code]}
          </option>
        ))}
      </select>
    </div>
  );
}
