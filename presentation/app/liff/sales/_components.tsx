"use client";

import Script from "next/script";
import { ReactNode } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { LIFF_SDK_SRC, Membership } from "./_lib";

/**
 * The shell every Sales dashboard page sits in.
 *
 * It exists mainly for the back link. Pages open inside the LINE in-app
 * browser, which has no address bar and no back button of its own, so
 * without this a person who taps into Deals from the menu has no way back
 * except closing the whole window and starting again.
 */
export function AppShell({
  title,
  back = "/liff/sales",
  liffId,
  onReady,
  onSdkError,
  status,
  statusTone,
  children,
}: {
  title: string;
  back?: string | null;
  liffId: string;
  onReady: () => void;
  onSdkError: () => void;
  status?: string;
  statusTone?: "ok" | "error";
  children: ReactNode;
}) {
  const { t } = useLanguage();
  return (
    <>
      <Script src={LIFF_SDK_SRC} strategy="afterInteractive" onReady={onReady} onError={onSdkError} />
      <div className="shell">
        <header className="topbar">
          {/* Plain anchor rather than next/link: each page
              initialises LIFF on load, and a full navigation
              guarantees a clean init instead of relying on the SDK
              surviving a client-side route change inside the LINE
              webview — which is what made every tap bounce back to
              the menu. */}
          {back && (
            /* Plain anchor rather than next/link: each page initialises
               LIFF on load, and a full navigation guarantees a clean init
               instead of relying on the SDK surviving a client-side route
               change inside the LINE webview. */
            <a className="backlink" href={back} aria-label={t.dashboard.back}>
              ←
            </a>
          )}
          <h1>{title}</h1>
          {/* The switcher lives in the bar so it is reachable from every
              page, which is what Phase 5 asks for — a language choice that
              only exists on one screen is not a language choice. */}
          <div style={{ marginLeft: "auto" }}>
            <LanguageSwitcher />
          </div>
        </header>
        <div className="page">
          {status ? (
            <p className="status" data-tone={statusTone} aria-live="polite">
              {status}
            </p>
          ) : (
            // Kept in the tree even when empty so screen readers keep
            // watching the same node for updates.
            <p className="status" aria-live="polite" />
          )}
          {children}
        </div>
      </div>
      {/* liffId is threaded through for pages that need it in a data
          attribute for debugging; unused visually. */}
      <span hidden data-liff-id={liffId} />
    </>
  );
}

export function CompanyPicker({
  memberships,
  licenseId,
  onChange,
}: {
  memberships: Membership[];
  licenseId: string;
  onChange: (id: string) => void;
}) {
  const { t } = useLanguage();
  // One company is the normal case and a select with a single option is
  // noise, so it only appears when there is a real choice to make.
  if (memberships.length <= 1) return null;
  return (
    <label className="field">
      <span>{t.dashboard.company}</span>
      <select value={licenseId} onChange={(event) => onChange(event.target.value)}>
        {memberships.map((membership) => (
          <option key={membership.license_id} value={membership.license_id}>
            {membership.company_name} ({membership.license_code})
          </option>
        ))}
      </select>
    </label>
  );
}

export function Badge({ stage, label }: { stage: string; label: string }) {
  return (
    <span className="badge" data-stage={stage}>
      {label}
    </span>
  );
}

/** An empty screen is an invitation to act, so it always names the action. */
export function Empty({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <p>{message}</p>
      {action}
    </div>
  );
}

export function Count({ shown, total }: { shown: number; total: number }) {
  const { t } = useLanguage();
  if (!total) return null;
  return (
    <p className="count">
      {shown === total
        ? t.dashboard.itemCount.replace("{total}", String(total))
        : t.dashboard.itemCountOf
            .replace("{shown}", String(shown))
            .replace("{total}", String(total))}
    </p>
  );
}
