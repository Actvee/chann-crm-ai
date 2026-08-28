"use client";

import Script from "next/script";
import { ReactNode, useEffect, useState } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { LIFF_SDK_SRC, Membership, liffDiagnostics } from "./_lib";

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
  // Recomputed on a tick rather than once: liff's own state changes as the
  // SDK loads and initialises, and a value captured at first render would
  // always read "sdk=missing".
  const [diagnostics, setDiagnostics] = useState("");
  useEffect(() => {
    const update = () => setDiagnostics(liffDiagnostics());
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, []);

  // Start on window.liff appearing, not only on next/script's onReady.
  //
  // onReady is a single callback with no retry: if it does not fire — the
  // script 404s, the CDN is slow, the callback is missed on a re-mount —
  // initialise is simply never called, the status stays on its opening
  // message and the page spins forever with no error to report. Polling for
  // the global the script defines is independent of that callback firing,
  // and the guard makes a double-start harmless if it does fire too.
  useEffect(() => {
    let started = false;
    const begin = () => {
      if (started) return;
      started = true;
      onReady();
    };
    if (typeof window !== "undefined" && (window as { liff?: unknown }).liff) {
      begin();
      return;
    }
    const poll = setInterval(() => {
      if ((window as { liff?: unknown }).liff) {
        clearInterval(poll);
        begin();
      }
    }, 200);
    // If the SDK never arrives, say so rather than spinning indefinitely.
    const giveUp = setTimeout(() => {
      clearInterval(poll);
      if (!started) onSdkError();
    }, 20_000);
    return () => {
      clearInterval(poll);
      clearTimeout(giveUp);
    };
    // Deliberately once on mount: onReady/onSdkError are recreated every
    // render by the pages, and depending on them would restart the sequence
    // on each state change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      {/* Loads the SDK; startup is driven by the poll above rather than
          by onReady, for the reason given there. */}
      <Script src={LIFF_SDK_SRC} strategy="afterInteractive" onError={onSdkError} />
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
              {/* Shown whenever the page has not finished starting, not only
                  after an error. An earlier version only showed this on
                  failure, which is exactly no help when the symptom is a
                  spinner that never resolves and therefore never produces
                  one. */}
              <span
                className="code"
                style={{ display: "block", marginTop: 6, fontSize: 11.5 }}
              >
                {diagnostics}
              </span>
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
