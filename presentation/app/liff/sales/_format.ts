"use client";

import { useCallback } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import type { Dictionary } from "@/lib/i18n";

import type { SalesText } from "./_strings";
import { useSalesText } from "./_strings";

/** BCP-47 tag for the chosen UI language (review C11: th-TH was hardcoded
 *  in money and date formatting, so the EN screen showed Thai digits and
 *  the Buddhist year). */
export function intlLocale(locale: string): string {
  return locale === "en" ? "en-US" : "th-TH";
}

export function useFormatters() {
  const { locale } = useLanguage();
  const tag = intlLocale(locale);
  const money = useCallback(
    (value: unknown, digits = 2): string => {
      const n = Number(value ?? 0);
      return Number.isFinite(n)
        ? n.toLocaleString(tag, { minimumFractionDigits: digits, maximumFractionDigits: digits })
        : "—";
    },
    [tag],
  );
  const shortDate = useCallback(
    (value: string | null | undefined): string => {
      if (!value) return "";
      const d = new Date(value);
      if (Number.isNaN(d.getTime())) return "";
      return d.toLocaleDateString(tag, { day: "numeric", month: "short", year: "2-digit" });
    },
    [tag],
  );
  return { money, shortDate, tag };
}

/** What the API said when it refused. Everything a page might want to
 *  show, parsed once; `describe` below turns it into the reader's words. */
export type ApiFailure = {
  status: number;
  /** A string detail, or the `message` of a structured one. */
  message: string;
  /** `error` / `reason_code` of a structured body, e.g. "duplicate". */
  code: string;
  /** Dispatch gate: the column names still missing. */
  missingFields: string[];
  /** Dispatch gate (Thai labels) or company profile (field names). */
  missing: string[];
  existingCode: string;
};

export async function readFailure(response: Response): Promise<ApiFailure> {
  let detail: unknown = "";
  try {
    detail = ((await response.clone().json()) as { detail?: unknown }).detail;
  } catch {
    detail = "";
  }
  if (detail && typeof detail === "object") {
    const body = detail as Record<string, unknown>;
    return {
      status: response.status,
      message: String(body.message ?? ""),
      code: String(body.reason_code ?? body.error ?? ""),
      missingFields: Array.isArray(body.missing_fields) ? body.missing_fields.map(String) : [],
      missing: Array.isArray(body.missing) ? body.missing.map(String) : [],
      existingCode: String(body.existing_code ?? ""),
    };
  }
  return {
    status: response.status,
    message: typeof detail === "string" ? detail : "",
    code: "",
    missingFields: [],
    missing: [],
    existingCode: "",
  };
}

/** The dispatch gate's column names in the reader's language. */
export function dispatchFieldLabels(fields: string[], t: Dictionary): string[] {
  const labels: Record<string, string> = {
    customer_name: t.dashboard.tickets.customer,
    customer_phone: t.dashboard.tickets.phone,
    service_address: t.dashboard.tickets.address,
    scheduled_date: t.dashboard.tickets.scheduledDate,
    scheduled_time: t.dashboard.tickets.scheduledTime,
    serial_number: t.dashboard.tickets.serial,
  };
  return fields.map((field) => labels[field] ?? field);
}

/** The company-profile field names in the reader's language. */
export function companyFieldLabels(fields: string[], t: Dictionary): string[] {
  const c = t.dashboard.companyProfile;
  const labels: Record<string, string> = {
    legal_name: c.legalName,
    tax_id: c.taxId,
    company_address: c.address,
    company_phone: c.phone,
    company_email: c.email,
    vat_rate: c.vat,
  };
  return fields.map((field) => labels[field] ?? field);
}

/**
 * One sentence for a refused request, in the reader's language.
 *
 * Reason codes first (the API sends one where the UI needs to translate),
 * then the status code. The raw English message is appended only when
 * there is no better wording, so a new refusal is still visible rather
 * than hidden behind "(409)".
 */
export function describeFailure(
  failure: ApiFailure, t: Dictionary, s: SalesText,
): string {
  const reasons = s.reasons as Record<string, string>;
  if (failure.code === "dispatch_blocked") {
    const names = failure.missingFields.length
      ? dispatchFieldLabels(failure.missingFields, t)
      : failure.missing;
    return reasons.dispatch_blocked.replace("{fields}", names.join(", "));
  }
  if (failure.code === "company_incomplete") {
    return reasons.company_incomplete.replace(
      "{fields}", companyFieldLabels(failure.missing, t).join(", "),
    );
  }
  if (failure.code === "duplicate") {
    return reasons.duplicate.replace("{code}", failure.existingCode || "—");
  }
  if (failure.code && reasons[failure.code]) return reasons[failure.code];
  switch (failure.status) {
    case 423:
      return s.errors.suspended;
    case 403:
      return s.errors.forbidden;
    case 404:
      return s.errors.notFound;
    case 409:
      return failure.message ? `${s.errors.conflict}: ${failure.message}` : s.errors.conflict;
    case 422:
      return failure.message ? `${s.errors.invalid}: ${failure.message}` : s.errors.invalid;
    default:
      return s.errors.failed.replace("{status}", String(failure.status));
  }
}

/** The two hooks a page needs to turn a Response into a sentence. */
export function useFailureText() {
  const { t } = useLanguage();
  const s = useSalesText();
  return useCallback(
    async (response: Response): Promise<string> => describeFailure(await readFailure(response), t, s),
    [t, s],
  );
}
