"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import Script from "next/script";
import { useCallback, useEffect, useState } from "react";

import { LanguageSwitcher } from "@/lib/i18n/LanguageSwitcher";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import PipelineSummary from "./PipelineSummary";
import { useFailureText } from "./_format";
import { LIFF_SDK_SRC, completeLiffRedirect, proxyHeaders, whenLiffReady } from "./_lib";
import { useSalesSession } from "./_session";
import { useSalesText } from "./_strings";
import { ShopSwitcher } from "../_shop-switcher";
import { SuspendedNotice } from "../_suspended";

/**
 * The Sales dashboard index — the page every other one links back to.
 *
 * This project is chat-first, and the LIFF pages that existed before were
 * reachable only by knowing their URL. This is the hub each new phase adds
 * a tile to, so a page shipped later is discoverable without anyone having
 * to remember it exists.
 */

const SECTIONS = [
  { href: "/liff/sales/chats", key: "chats" },
  { href: "/liff/sales/customers", key: "customers" },
  { href: "/liff/sales/deals", key: "deals" },
  { href: "/liff/sales/quotes", key: "quotes" },
    { href: "/liff/sales/tickets", key: "tickets" },
    { href: "/liff/sales/reports", key: "reports" },
    { href: "/liff/sales/reports/ai", key: "aiReports" },
  { href: "/liff/sales/approvals", key: "approvals" },
  { href: "/liff/sales/warranties", key: "warranties" },
  { href: "/liff/sales/teams", key: "teams" },
  { href: "/liff/sales/guide", key: "guide" },
  { href: "/liff/sales/products", key: "products" },
  { href: "/liff/sales/templates", key: "templates" },
  { href: "/liff/sales/company", key: "company" },
  { href: "/liff/sales/members", key: "members" },
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
  // Bumped when the shop changes so the pipeline card starts again in
  // the new shop rather than showing the old one's numbers.
  const [shopEpoch, setShopEpoch] = useState(0);

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
    chats: t.dashboard.chats.title,
    customers: t.customer.title,
    deals: t.deal.title,
    quotes: t.quote.title,
    products: t.product.title,
    company: t.dashboard.companyTitle,
    members: t.dashboard.members.title,
    roles: t.role.title,
    tickets: t.dashboard.tickets.title,
    reports: t.dashboard.reports.title,
    aiReports: t.dashboard.aiReports.title,
    approvals: t.dashboard.approvals.title,
    templates: t.dashboard.templates.title,
    warranties: t.dashboard.warranties.title,
    teams: t.dashboard.teams.title,
    guide: t.dashboard.guide.title,
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
        <div className="topbar-tools" style={{ marginLeft: 0, justifyContent: "space-between" }}>
          <h1>{t.dashboard.menuTitle}</h1>
          <a className="guidelink" href="/liff/sales/guide">
            <span aria-hidden="true">?</span>
            {t.dashboard.guide.title}
          </a>
        </div>
        <div style={{ marginLeft: "auto" }}>
          <LanguageSwitcher />
        </div>
      </header>
      <div className="page">
        <MenuSession liffId={liffId} onShopChanged={() => setShopEpoch((n) => n + 1)} />
        <p style={{ color: "var(--ink-soft)", fontSize: 14.5, margin: "0 0 16px" }}>
          {t.dashboard.menuIntro}
        </p>

        <PipelineSummary key={shopEpoch} liffId={liffId} />

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

type Transfer = { id: string; status: string; to_chann_uid?: string | null };

/**
 * The menu itself needs no session; three things on it do: the
 * suspended-shop notice, the shop switcher for someone who belongs to
 * several (review C5), and the "accept ownership" banner for a member
 * the owner has nominated (E6). Asks once and stays silent otherwise.
 */
function MenuSession({ liffId, onShopChanged }: { liffId: string; onShopChanged: () => void }) {
  const { t } = useLanguage();
  const s = useSalesText();
  const failureText = useFailureText();
  const [note, setNote] = useState<{ text: string; tone?: "ok" | "error" } | null>(null);
  const say = useCallback((text: string, tone?: "ok" | "error") => {
    // The menu has no status line; init failures stay in the console,
    // outcomes of the banner's own button show under it.
    if (tone) setNote({ text, tone });
  }, []);
  const session = useSalesSession(liffId, say);
  const [offer, setOffer] = useState<Transfer | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    whenLiffReady()
      .then(() => session.initialize())
      .catch(() => undefined);
    return () => {
      alive = false;
      void alive;
    };
    // Once on mount: initialize is stable per liffId.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liffId]);

  useEffect(() => {
    if (!session.ready || session.isOwner) {
      setOffer(null);
      return;
    }
    let alive = true;
    void (async () => {
      try {
        const response = await fetch(
          `/api/phase2/licenses/${session.licenseId}/ownership-transfers`,
          { headers: proxyHeaders(session.token, session.licenseId) },
        );
        if (!response.ok || !alive) return;
        const rows = (await response.json()) as Transfer[];
        setOffer(rows.find((r) => r.status === "pending" && r.to_chann_uid === session.channUid) ?? null);
      } catch {
        // No banner is the right outcome for a failed lookup.
      }
    })();
    return () => {
      alive = false;
    };
  }, [session.ready, session.isOwner, session.licenseId, session.token, session.channUid]);

  const shop = session.memberships[0];

  async function accept() {
    if (!offer || !shop) return;
    if (!window.confirm(s.menu.confirmAccept.replace("{shop}", shop.company_name))) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${session.licenseId}/ownership-transfers/${offer.id}/accept`,
        { method: "POST", headers: proxyHeaders(session.token, session.licenseId) },
      );
      if (!response.ok) {
        setNote({ text: await failureText(response), tone: "error" });
        return;
      }
      setOffer(null);
      setNote({ text: s.menu.accepted, tone: "ok" });
      await session.initialize();
    } catch {
      setNote({ text: t.common.error, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SuspendedNotice memberships={session.memberships} />
      {session.memberships.length > 1 && (
        <ShopSwitcher
          token={session.token}
          audience="sales"
          shops={session.memberships}
          current={session.licenseId}
          label={t.dashboard.customer.shopSwitch}
          onSwitched={() => {
            void session.switchShop().then(onShopChanged);
          }}
        />
      )}
      {offer && shop && (
        <div className="callout" data-tone="warn" role="status">
          <span className="dot" />
          <span style={{ flex: 1 }}>
            {s.menu.transferOffer.replace("{shop}", shop.company_name)}
          </span>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            disabled={busy}
            onClick={() => void accept()}
          >
            {busy ? t.dashboard.working : s.menu.accept}
          </button>
        </div>
      )}
      {note && (
        <p className="status" data-tone={note.tone} aria-live="polite">{note.text}</p>
      )}
    </>
  );
}
