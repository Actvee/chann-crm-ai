"use client";

import Script from "next/script";
import { useEffect, useState } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { LIFF_SDK_SRC, followLiffState } from "./_lib";

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
  { href: "/liff/sales/products", key: "products" },
  { href: "/liff/sales/company", key: "company" },
  { href: "/liff/sales/roles", key: "roles" },
] as const;

const BASE_PATH = "/liff/sales";

export default function SalesMenu() {
  const { t } = useLanguage();
  // Deep links from chat arrive here, not at the page they name: LINE loads
  // the LIFF app's endpoint URL and leaves the rest of the path in a
  // `liff.state` query parameter for the page to follow. Without this the
  // "open dashboard" button always landed on the menu regardless of which
  // list it was tapped from.
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    // Runs immediately and does not wait for the SDK: liff.state is a plain
    // query parameter, so following it needs no LIFF at all. Waiting for
    // the SDK here would delay every deep link behind a script download for
    // no reason, and would strand the link entirely if that download failed.
    if (followLiffState(BASE_PATH)) setRedirecting(true);
  }, []);
  const titles: Record<string, string> = {
    customers: t.customer.title,
    deals: t.deal.title,
    quotes: t.quote.title,
    products: t.product.title,
    company: t.dashboard.companyTitle,
    roles: t.role.title,
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

        <ul className="tiles">
          {SECTIONS.map((section) => (
            <li key={section.key}>
              {/* A plain anchor, not next/link: these pages each initialise
                  LIFF on load, and a full navigation guarantees a clean
                  init rather than depending on the SDK surviving a
                  client-side route change inside the LINE webview. */}
              <a className="tile" href={section.href}>
                <h2>{titles[section.key]}</h2>
                <p>{t.dashboard.sections[section.key]}</p>
              </a>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
