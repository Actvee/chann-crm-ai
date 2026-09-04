"use client";

import Link from "next/link";
import Script from "next/script";
import { ReactNode, useEffect, useState } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { usePathname, useRouter } from "next/navigation";

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
  notice,
  nav = true,
  guideHref,
  wide = false,
  children,
}: {
  title: string;
  back?: string | null;
  /** Where "วิธีใช้" goes. Derived from the path when not given, so every
   *  page of every OA carries it in the same spot (owner, 4 Sep). */
  guideHref?: string | null;
  /** Two-pane pages (the chat inbox) get the wider shell. */
  wide?: boolean;
  /** The Sales section strip. Off for pages a technician or customer
   *  opens — the strip is Sales' furniture, and on the technician's
   *  reports page it made the page read as the wrong OA (owner, 3 Sep). */
  nav?: boolean;
  liffId: string;
  onReady: () => void;
  onSdkError: () => void;
  status?: string;
  statusTone?: "ok" | "error";
  /** A page-wide notice above the content (a suspended shop). */
  notice?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useLanguage();
  const pathname = usePathname() ?? "";
  const audience = pathname.startsWith("/liff/customer")
    ? "customer"
    : pathname.startsWith("/liff/technician")
      ? "technician"
      : "sales";
  const guide = guideHref === null ? null : guideHref ?? `/liff/${audience}/guide`;
  const onGuidePage = pathname.endsWith("/guide");
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
      <div className="shell" data-wide={wide ? "true" : undefined}>
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
            <Link className="backlink" href={back} aria-label={t.dashboard.back}>
              ←
            </Link>
          )}
          <h1>{title}</h1>
          {/* The switcher lives in the bar so it is reachable from every
              page, which is what Phase 5 asks for — a language choice that
              only exists on one screen is not a language choice. */}
          <div className="topbar-tools">
            {guide && !onGuidePage && (
              <a className="guidelink" href={guide}>
                <span aria-hidden="true">?</span>
                {t.dashboard.guide.title}
              </a>
            )}
            <LanguageSwitcher />
          </div>
        </header>
        {/* Section navigation, on every Sales page. Without it, moving
            from a deal to the quote list meant back to the menu and out
            again — two taps and a full page for what every CRM does with
            a tab strip. Scrolls sideways on a phone rather than wrapping,
            so it stays one row. */}
        {back && nav && <SectionNav />}
        <div className="page">
          {status ? (
            <p className="status" data-tone={statusTone} aria-live="polite">
              {/* A spinner while starting; plain text once there is something
                  to say. The LIFF diagnostics that used to live here were
                  debugging output and read as a fault to anyone who was not
                  the developer — they belong in the console, which is where
                  they now are. */}
              {statusTone === undefined && <span className="spinner" aria-hidden="true" />}
              {status}
            </p>
          ) : (
            // Kept in the tree even when empty so screen readers keep
            // watching the same node for updates.
            <p className="status" aria-live="polite" />
          )}
          {notice}
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


const NAV = [
  { href: "/liff/sales/chats", key: "chats" },
  { href: "/liff/sales/customers", key: "customers" },
  { href: "/liff/sales/deals", key: "deals" },
  { href: "/liff/sales/quotes", key: "quotes" },
  { href: "/liff/sales/tickets", key: "tickets" },
  { href: "/liff/sales/reports", key: "reports" },
  { href: "/liff/sales/approvals", key: "approvals" },
  { href: "/liff/sales/products", key: "products" },
] as const;

/** The strip of sections under the header — where you are, and where else you can go. */
function SectionNav() {
  const { t } = useLanguage();
  const pathname = usePathname();
  const router = useRouter();
  const labels: Record<string, string> = {
    chats: t.dashboard.chats.title,
    approvals: t.dashboard.approvals.title,
    customers: t.customer.title,
    deals: t.deal.title,
    quotes: t.quote.title,
    tickets: t.dashboard.tickets.title,
    reports: t.dashboard.reports.title,
    products: t.product.title,
  };
  return (
    <nav className="section-nav" aria-label="sections">
      {NAV.map((item) => {
        const active = pathname?.startsWith(item.href);
        return (
          <button
            key={item.key}
            type="button"
            data-active={active ? "true" : undefined}
            aria-current={active ? "page" : undefined}
            // router.push, not an anchor: a full page load loses the
            // LIFF session (see the note on tiles in SalesMenu).
            onClick={() => router.push(item.href)}
          >
            {labels[item.key]}
          </button>
        );
      })}
    </nav>
  );
}
