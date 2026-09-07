"use client";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import type { Membership } from "./_shared";

/** The shop the screen is showing: the selected one when the page has a
 *  shop switcher, the first (only) one otherwise. A person in several
 *  shops used to see the FIRST shop's suspension on every shop's home
 *  (review D7, 6 Sep 2026). */
function shopOf(memberships: Membership[], current?: string | null): Membership | undefined {
  if (current) return memberships.find((m) => m.license_id === current) ?? memberships[0];
  return memberships[0];
}

/** Phase 18 follow-up (4 Sep 2026): a suspended shop is read-only. The
 *  chat already says so; the dashboards now show the same notice at the
 *  top of every home screen, from the membership the session carries. */
export function SuspendedNotice({
  memberships,
  current,
}: {
  memberships: Membership[];
  /** license_id of the shop currently shown; defaults to the first. */
  current?: string | null;
}) {
  const { t } = useLanguage();
  const shop = shopOf(memberships, current);
  if (!shop || shop.license_status !== "suspended") return null;
  return (
    <div className="callout" role="status" data-tone="warn">
      <strong>{t.dashboard.suspended.title.replace("{shop}", shop.company_name)}</strong>
      <div>{t.dashboard.suspended.body}</div>
    </div>
  );
}

export function isSuspended(memberships: Membership[], current?: string | null): boolean {
  return shopOf(memberships, current)?.license_status === "suspended";
}
