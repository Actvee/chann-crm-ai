"use client";

import type { ReactNode } from "react";

import type { Dictionary } from "@/lib/i18n";

import type { Audience } from "./_shared";

/**
 * What the left navigation offers, per OA.
 *
 * Sixteen sections in one flat list is a wall — the owner's complaint on
 * 8 Sep was that the dashboard is hard to use, and an undifferentiated
 * list of sixteen is the reason. They are grouped here by the job being
 * done: selling, service, the paperwork those two produce, and running
 * the shop. Four labels, none longer than a glance.
 *
 * `needs` is the permission the API itself checks on that page's first
 * GET (routers_phase2.py). A link drawn without it would open a page that
 * immediately says "คุณไม่มีสิทธิ์" — offering it is worse than not
 * showing it, so entries whose key is missing are not rendered at all.
 */

export type NavEntry = {
  key: string;
  href: string;
  label: string;
  icon: ReactNode;
  /** Any one of these keys is enough; absent means the page is open to all. */
  needs?: readonly string[];
  /** Hub pages match on equality; everything else on path prefix. */
  exact?: boolean;
};

export type NavGroup = {
  key: string;
  /** null renders the entries with no heading (the short technician and
   *  customer menus, where a heading would label a list of four). */
  label: string | null;
  entries: NavEntry[];
};

/** One drawing surface for every icon: 24px box, 1.8 stroke, round joins.
 *  Inline SVG rather than a font or an emoji — an emoji is whatever the
 *  phone decides it is, and cannot take the OA's colour. */
function glyph(children: ReactNode): ReactNode {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const ICONS = {
  overview: glyph(<path d="M3 10.6 12 4l9 6.6V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />),
  chats: glyph(<path d="M20 14.5a2.5 2.5 0 0 1-2.5 2.5H8.5L4 20.5V6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5z" />),
  customers: glyph(
    <>
      <circle cx="9.5" cy="7.5" r="3.5" />
      <path d="M16 20.5v-1.6a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1.6" />
      <path d="M17 4.6a3.5 3.5 0 0 1 0 6.8M21 20.5v-1.6a4 4 0 0 0-3-3.86" />
    </>,
  ),
  deals: glyph(
    <>
      <path d="M3 17.5 9.5 11l4 4L21 7.5" />
      <path d="M15.5 7.5H21v5.5" />
    </>,
  ),
  quotes: glyph(
    <>
      <path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z" />
      <path d="M14 3v4h4M9 12.5h6M9 16.5h4" />
    </>,
  ),
  products: glyph(
    <>
      <path d="M21 8.5 12 4 3 8.5v7L12 20l9-4.5z" />
      <path d="m3 8.5 9 4.5 9-4.5M12 13v7" />
    </>,
  ),
  tickets: glyph(
    <path d="M15.4 3.5a5 5 0 0 0-5.8 6.6L3.7 16a2.1 2.1 0 0 0 2.9 2.9l5.9-5.9a5 5 0 0 0 6.6-5.8l-2.9 2.9-2.7-2.7z" />,
  ),
  warranties: glyph(
    <>
      <path d="M12 3.2 5 5.9v5.4c0 4 2.9 7.3 7 8.8 4.1-1.5 7-4.8 7-8.8V5.9z" />
      <path d="m9 12 2.2 2.2L15.2 10" />
    </>,
  ),
  teams: glyph(
    <>
      <circle cx="8.5" cy="8" r="3.2" />
      <circle cx="17" cy="9" r="2.4" />
      <path d="M2.8 19.5v-1a4 4 0 0 1 4-4h3.4a4 4 0 0 1 4 4v1" />
      <path d="M15.6 14.5H17a4 4 0 0 1 4 4v1" />
    </>,
  ),
  reports: glyph(
    <>
      <path d="M8.5 4.5H6a1 1 0 0 0-1 1V20a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V5.5a1 1 0 0 0-1-1h-2.5" />
      <path d="M9 3h6v3H9z" />
      <path d="M9 12h6M9 16h4" />
    </>,
  ),
  approvals: glyph(
    <>
      <circle cx="12" cy="12" r="8.6" />
      <path d="m8 12.2 2.8 2.8L16.2 9.6" />
    </>,
  ),
  aiReports: glyph(
    <>
      <path d="M3.5 20.5h17M7.5 20.5v-5.5M12 20.5V11M16.5 20.5v-3.5" />
      <path d="m18.6 3 .8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
    </>,
  ),
  templates: glyph(
    <>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M4 9.5h16M9.5 20V9.5" />
    </>,
  ),
  company: glyph(
    <>
      <path d="M4 21V4.8a.8.8 0 0 1 .8-.8h8.4a.8.8 0 0 1 .8.8V21" />
      <path d="M14 10.5h5.2a.8.8 0 0 1 .8.8V21M3 21h18M7.5 8h3M7.5 12h3M7.5 16h3" />
    </>,
  ),
  members: glyph(
    <>
      <circle cx="9.5" cy="7.5" r="3.5" />
      <path d="M15 20.5v-1.6a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1.6" />
      <path d="m16.4 11.4 2 2 3.6-3.8" />
    </>,
  ),
  roles: glyph(
    <>
      <circle cx="7.6" cy="15.4" r="3.6" />
      <path d="M10.2 12.8 20 3M17.2 5.8l2 2M14.6 8.4l2 2" />
    </>,
  ),
  guide: glyph(
    <>
      <circle cx="12" cy="12" r="8.6" />
      <path d="M9.6 9.3a2.5 2.5 0 0 1 4.9.7c0 1.7-2.4 1.9-2.4 3.4" />
      <path d="M12 17.1h.01" />
    </>,
  ),
  signature: glyph(
    <>
      <path d="M3 19.5c3 0 3-2.6 6-2.6s3 2.6 6 2.6 3-1 3-1" />
      <path d="m12.6 12.4 6-6a1.9 1.9 0 0 0-2.7-2.7l-6 6-1 3.7z" />
    </>,
  ),
  menu: glyph(<path d="M4 7h16M4 12h16M4 17h16" />),
  close: glyph(<path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />),
  collapse: glyph(<path d="M14.5 6.5 9 12l5.5 5.5" />),
  expand: glyph(<path d="M9.5 6.5 15 12l-5.5 5.5" />),
} as const;

/**
 * The permission each page's first request checks, taken from the route
 * decorators rather than guessed: chat-sessions → chat_session.view,
 * members → member.manage/team.manage/role.manage, and so on.
 */
export function navGroups(t: Dictionary, audience: Audience): NavGroup[] {
  const nav = t.dashboard.nav;

  if (audience === "technician") {
    return [
      {
        key: "technician",
        label: null,
        entries: [
          { key: "home", href: "/liff/technician", label: t.dashboard.technician.home, icon: ICONS.overview, exact: true },
          { key: "tickets", href: "/liff/technician/tickets", label: t.dashboard.tickets.title, icon: ICONS.tickets },
          { key: "reports", href: "/liff/technician/reports", label: t.dashboard.reports.title, icon: ICONS.reports },
          { key: "signature", href: "/liff/technician/signature", label: t.dashboard.signature.title, icon: ICONS.signature },
        ],
      },
    ];
  }

  if (audience === "customer") {
    return [
      {
        key: "customer",
        label: null,
        entries: [
          { key: "home", href: "/liff/customer", label: t.dashboard.customer.home, icon: ICONS.overview, exact: true },
          { key: "tickets", href: "/liff/customer/tickets", label: t.dashboard.tickets.title, icon: ICONS.tickets },
          { key: "reports", href: "/liff/customer/reports", label: t.dashboard.reports.title, icon: ICONS.reports },
          { key: "signature", href: "/liff/customer/signature", label: t.dashboard.signature.title, icon: ICONS.signature },
        ],
      },
    ];
  }

  return [
    {
      key: "start",
      label: null,
      entries: [
        { key: "overview", href: "/liff/sales", label: nav.overview, icon: ICONS.overview, exact: true },
      ],
    },
    {
      key: "selling",
      label: nav.groups.selling,
      entries: [
        { key: "chats", href: "/liff/sales/chats", label: t.dashboard.chats.title, icon: ICONS.chats, needs: ["chat_session.view"] },
        { key: "customers", href: "/liff/sales/customers", label: t.customer.title, icon: ICONS.customers, needs: ["customer.read"] },
        { key: "deals", href: "/liff/sales/deals", label: t.deal.title, icon: ICONS.deals, needs: ["deal.read"] },
        { key: "quotes", href: "/liff/sales/quotes", label: t.quote.title, icon: ICONS.quotes, needs: ["quote.read"] },
        { key: "products", href: "/liff/sales/products", label: t.product.title, icon: ICONS.products, needs: ["product.read", "product.manage"] },
      ],
    },
    {
      key: "service",
      label: nav.groups.service,
      entries: [
        { key: "tickets", href: "/liff/sales/tickets", label: t.dashboard.tickets.title, icon: ICONS.tickets, needs: ["ticket.read"] },
        { key: "warranties", href: "/liff/sales/warranties", label: t.dashboard.warranties.title, icon: ICONS.warranties, needs: ["warranty.read"] },
        { key: "teams", href: "/liff/sales/teams", label: t.dashboard.teams.title, icon: ICONS.teams, needs: ["ticket.read"] },
      ],
    },
    {
      key: "paperwork",
      label: nav.groups.paperwork,
      entries: [
        // Ordered before /liff/sales/reports so the AI view is matched by
        // the longer prefix first when both would light up.
        { key: "reports", href: "/liff/sales/reports", label: t.dashboard.reports.title, icon: ICONS.reports, needs: ["ticket.read"] },
        { key: "aiReports", href: "/liff/sales/reports/ai", label: t.dashboard.aiReports.title, icon: ICONS.aiReports, needs: ["view_reports"] },
        { key: "approvals", href: "/liff/sales/approvals", label: t.dashboard.approvals.title, icon: ICONS.approvals, needs: ["approval.view"] },
        { key: "templates", href: "/liff/sales/templates", label: t.dashboard.templates.title, icon: ICONS.templates, needs: ["setting.manage"] },
      ],
    },
    {
      key: "shop",
      label: nav.groups.shop,
      entries: [
        { key: "company", href: "/liff/sales/company", label: t.dashboard.companyTitle, icon: ICONS.company, needs: ["setting.manage"] },
        { key: "members", href: "/liff/sales/members", label: t.dashboard.members.title, icon: ICONS.members, needs: ["member.manage", "team.manage", "role.manage"] },
        { key: "roles", href: "/liff/sales/roles", label: t.role.title, icon: ICONS.roles, needs: ["role.manage"] },
      ],
    },
  ];
}

/** "วิธีใช้" sits at the foot of the rail on every OA, next to where the
 *  same link sits in the top bar — help is not a section of the product. */
export function guideEntry(t: Dictionary, audience: Audience): NavEntry {
  return {
    key: "guide",
    href: `/liff/${audience}/guide`,
    label: t.dashboard.guide.title,
    icon: ICONS.guide,
  };
}

/**
 * Whether to draw an entry.
 *
 * Fails open on an empty set, the same direction `fetchPermissions`
 * documents: an unanswered /me must not empty the menu of a person who
 * can in fact use every page on it. The owner sees everything.
 */
export function mayOpen(entry: NavEntry, permissions: Set<string>, isOwner: boolean): boolean {
  if (!entry.needs || entry.needs.length === 0) return true;
  if (isOwner) return true;
  if (permissions.size === 0) return true;
  return entry.needs.some((key) => permissions.has(key));
}

/** Start with the work this member can do; service staff share this OA. */
export function homeEntries(t: Dictionary, permissions: Set<string>, isOwner: boolean): NavEntry[] {
  const serviceFirst = !isOwner && !permissions.has("deal.read") &&
    (permissions.has("ticket.read") || permissions.has("approval.view"));
  const priority = serviceFirst
    ? ["chats", "tickets", "approvals", "reports"]
    : ["chats", "customers", "deals", "quotes"];
  const entries = navGroups(t, "sales")
    .filter((group) => group.key !== "start")
    .flatMap((group) => group.entries)
    .filter((entry) => mayOpen(entry, permissions, isOwner));
  const preferred = priority.flatMap((key) => entries.filter((entry) => entry.key === key));
  return [...preferred, ...entries.filter((entry) => !priority.includes(entry.key))].slice(0, 4);
}

/** The entry that owns the current path — longest matching href wins, so
 *  /liff/sales/reports/ai is the AI report and not the report list. */
export function currentKey(entries: NavEntry[], pathname: string): string | null {
  let best: NavEntry | null = null;
  for (const entry of entries) {
    const hit = entry.exact ? pathname === entry.href : pathname === entry.href || pathname.startsWith(`${entry.href}/`);
    if (!hit) continue;
    if (!best || entry.href.length > best.href.length) best = entry;
  }
  return best?.key ?? null;
}
