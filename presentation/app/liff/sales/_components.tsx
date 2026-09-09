"use client";

import Link from "next/link";
import Script from "next/script";
import { ReactNode, useEffect } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { usePathname } from "next/navigation";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { NavFrame, NavMenuButton } from "../_nav";
import { LIFF_SDK_SRC } from "./_lib";

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
  permissions,
  isOwner = false,
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
  /** The left navigation. On by default and correct for whichever OA the
   *  path belongs to — the old Sales-only section strip is gone, so a
   *  technician page no longer has to switch it off to avoid reading as
   *  the wrong OA (owner, 3 Sep). Off only for a surface with no menu. */
  nav?: boolean;
  /** What this person may open, from /me. Entries whose page would refuse
   *  them are not drawn; an unanswered /me (an empty set) shows the lot,
   *  the same direction fetchPermissions already fails in. */
  permissions?: Set<string>;
  isOwner?: boolean;
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
      {/* The rail sits outside the shell, not inside it: the shell is a
          640px reading column and the navigation is a sibling of it. */}
      <NavFrame audience={audience} permissions={permissions} isOwner={isOwner} enabled={nav}>
        <div className="shell" data-wide={wide ? "true" : undefined}>
          <header className="topbar">
            {/* Opens the drawer on a phone; absent once the rail is
                permanent. First in the bar so the menu is the first thing
                the tab order and the eye reach. */}
            <NavMenuButton />
            {back && (
              /* The way out of a page inside LINE's in-app browser, which
                 has no address bar and no back button of its own. Kept
                 exactly as it was — the rail is somewhere else to go, not
                 a way back. next/link so the LIFF session survives. */
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
      </NavFrame>
      {/* liffId is threaded through for pages that need it in a data
          attribute for debugging; unused visually. */}
      <span hidden data-liff-id={liffId} />
    </>
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

