"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import Script from "next/script";
import { useEffect, useState } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import PipelineSummary from "./PipelineSummary";
import { LIFF_SDK_SRC, completeLiffRedirect } from "./_lib";

/**
 * The Sales dashboard index — the page every other one links back to.
 *
 * This project is chat-first, and the LIFF pages that existed before were
 * reachable only by knowing their URL. This is the hub each new phase adds
 * a tile to, so a page shipped later is discoverable without anyone having
 * to remember it exists.
 */

const SECTIONS = [
  { href: "/liff/sales/customers", key: "customers" },
  { href: "/liff/sales/deals", key: "deals" },
  { href: "/liff/sales/quotes", key: "quotes" },
    { href: "/liff/sales/tickets", key: "tickets" },
    { href: "/liff/sales/reports", key: "reports" },
  { href: "/liff/sales/approvals", key: "approvals" },
  { href: "/liff/sales/warranties", key: "warranties" },
  { href: "/liff/sales/products", key: "products" },
  { href: "/liff/sales/templates", key: "templates" },
  { href: "/liff/sales/company", key: "company" },
  { href: "/liff/sales/roles", key: "roles" },
] as const;

export default function SalesMenu({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  // A deep link lands here with BOTH a `liff.state` naming the real
  // destination and a `code` that liff.init() must exchange for a token.
  //
  // Do not navigate on liff.state directly. An earlier version did, and it
  // moved the page to the target path before init() had consumed the code —
  // so the token was never obtained, LIFF requested authorisation again,
  // came back with a fresh code, and the same navigation threw that one away
  // too. The Cloud Run access log showed the cycle plainly: a different
  // `code=` value every ~700ms, forever.
  //
  // liff.init() handles liff.state itself: it exchanges the code, stores the
  // session, and then redirects to the target. All this page has to do is
  // call it and stay out of the way.
  const [redirecting, setRedirecting] = useState(false);
  const router = useRouter();

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!new URLSearchParams(window.location.search).has("liff.state")) return;
    setRedirecting(true);

    // Waits for the SDK: init() cannot run before the script exists, and
    // running the redirect without it is what caused the loop.
    let cancelled = false;
    const attempt = async () => {
      for (let i = 0; i < 100 && !cancelled; i += 1) {
        if ((window as { liff?: unknown }).liff) break;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      if (cancelled) return;
      const target = await completeLiffRedirect(liffId);
      if (cancelled) return;
      if (target) {
        router.replace(target);
      } else {
        // Nothing to follow, or init already moved us. Show the menu rather
        // than leaving a blank page, which is what a previous version did
        // when it assumed init would always redirect.
        setRedirecting(false);
      }
    };
    void attempt();
    return () => {
      cancelled = true;
    };
  }, [liffId, router]);
  const titles: Record<string, string> = {
    customers: t.customer.title,
    deals: t.deal.title,
    quotes: t.quote.title,
    products: t.product.title,
    company: t.dashboard.companyTitle,
    roles: t.role.title,
    tickets: t.dashboard.tickets.title,
    reports: t.dashboard.reports.title,
    approvals: t.dashboard.approvals.title,
    templates: t.dashboard.templates.title,
    warranties: t.dashboard.warranties.title,
  };

  if (redirecting) {
    // A blank frame for the instant before the navigation commits. Showing
    // the menu here would flash the wrong page on every deep link.
    return <div className="shell" />;
  }

  return (
    <div className="shell">
      {/* The SDK is loaded even though this page needs no session, because
          liff.state has to be read before anything else can happen and the
          SDK sets up the LIFF context the sub-pages then rely on. */}
      <Script src={LIFF_SDK_SRC} strategy="afterInteractive" />
      <header className="topbar">
        <h1>{t.dashboard.menuTitle}</h1>
        <div style={{ marginLeft: "auto" }}>
          <LanguageSwitcher />
        </div>
      </header>
      <div className="page">
        <p style={{ color: "var(--ink-soft)", fontSize: 14.5, margin: "0 0 16px" }}>
          {t.dashboard.menuIntro}
        </p>

        <PipelineSummary liffId={liffId} />

        <ul className="tiles">
          {SECTIONS.map((section) => (
            <li key={section.key}>
              {/* Client-side navigation, NOT a plain anchor.
                  A LIFF session exists only in the page LINE opened through
                  a LIFF URL. A full page load starts a fresh document with
                  no LIFF context at all — measured directly:
                  inClient=true but loggedIn=false — so every sub-page then
                  failed to authenticate. An earlier change to plain
                  anchors, made to "guarantee a clean init", is what caused
                  that. Staying in one document keeps the session. */}
              <Link className="tile" href={section.href}>
                <h2>{titles[section.key]}</h2>
                <p>{t.dashboard.sections[section.key]}</p>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
