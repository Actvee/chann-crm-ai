"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  DEFAULT_LOCALE,
  dictionaryFor,
  isLocale,
  readStoredLocale,
  writeStoredLocale,
  type Dictionary,
  type Locale,
} from "./index";

type Session = { token: string; audience: "customer" | "sales" | "technician" };

type LanguageContextValue = {
  locale: Locale;
  t: Dictionary;
  setLocale: (locale: Locale) => void;
  /**
   * Tie the page's language to the signed-in person's server-side
   * preference (Phase 16.3, `/display-preferences`): reads it once now and
   * writes it back on every later switch. Before this the screen read
   * localStorage while the chat read the server, and the two never agreed
   * (review D6, 6 Sep 2026). Best-effort: the page keeps working offline.
   */
  bindSession: (session: Session) => void;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  // Start from the default on both server and first client render, then adopt
  // the stored choice in an effect. Reading localStorage during render would
  // make the server and client markup disagree and trip hydration.
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);
  const session = useRef<Session | null>(null);

  useEffect(() => {
    const stored = readStoredLocale();
    if (stored !== locale) setLocaleState(stored);
    // deliberately once on mount — later changes go through setLocale
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = locale;
    }
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    writeStoredLocale(next);
    const bound = session.current;
    if (!bound) return;
    void fetch(`/api/liff/${bound.audience}/display-preferences`, {
      method: "PUT",
      headers: { "X-Liff-ID-Token": bound.token, "Content-Type": "application/json" },
      body: JSON.stringify({ language: next }),
    }).catch(() => undefined);
  }, []);

  const bindSession = useCallback((next: Session) => {
    if (!next.token) return;
    session.current = next;
    void (async () => {
      try {
        const response = await fetch(`/api/liff/${next.audience}/display-preferences`, {
          headers: { "X-Liff-ID-Token": next.token },
        });
        if (!response.ok) return;
        const prefs = (await response.json()) as { language?: string | null };
        if (isLocale(prefs.language)) {
          // The server's word wins over the browser's memory: it is what
          // the chat and every notification already speak.
          setLocaleState(prefs.language);
          writeStoredLocale(prefs.language);
        }
      } catch {
        /* the stored choice stands */
      }
    })();
  }, []);

  const value = useMemo<LanguageContextValue>(
    () => ({ locale, t: dictionaryFor(locale), setLocale, bindSession }),
    [locale, setLocale, bindSession],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) {
    throw new Error("useLanguage must be used inside <LanguageProvider>");
  }
  return ctx;
}
