"use client";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

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

export default function SalesMenu() {
  const { t } = useLanguage();
  const titles: Record<string, string> = {
    customers: t.customer.title,
    deals: t.deal.title,
    quotes: t.quote.title,
    products: t.product.title,
    company: t.dashboard.companyTitle,
    roles: t.role.title,
  };

  return (
    <div className="shell">
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
