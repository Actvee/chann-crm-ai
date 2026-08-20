import { en } from "./en";
import { th, type Dictionary } from "./th";

export type Locale = "th" | "en";
export type { Dictionary };

export const LOCALES: readonly Locale[] = ["th", "en"] as const;

/** Thai is the default: the product is Thai-first (Master Spec 5.1). */
export const DEFAULT_LOCALE: Locale = "th";

export const DICTIONARIES: Record<Locale, Dictionary> = { th, en };

export const STORAGE_KEY = "chann.locale";

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

export function dictionaryFor(locale: Locale): Dictionary {
  return DICTIONARIES[locale] ?? DICTIONARIES[DEFAULT_LOCALE];
}

/**
 * Read the persisted choice. Returns the default rather than throwing when
 * storage is unavailable (SSR, private mode, storage disabled) — a language
 * preference is never worth breaking a render over.
 */
export function readStoredLocale(): Locale {
  if (typeof window === "undefined") return DEFAULT_LOCALE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return isLocale(raw) ? raw : DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

export function writeStoredLocale(locale: Locale): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    /* preference simply will not persist; not worth surfacing to the user */
  }
}
