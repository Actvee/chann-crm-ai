"use client";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import type { Membership } from "./_shared";

/** Phase 18 follow-up (4 Sep 2026): a suspended shop is read-only. The
 *  chat already says so; the dashboards now show the same notice at the
 *  top of every home screen, from the membership the session carries. */
export function SuspendedNotice({ memberships }: { memberships: Membership[] }) {
  const { t } = useLanguage();
  const shop = memberships[0];
  if (!shop || shop.license_status !== "suspended") return null;
  return (
    <div className="callout" role="status" data-tone="warn">
      <strong>{t.dashboard.suspended.title.replace("{shop}", shop.company_name)}</strong>
      <div>{t.dashboard.suspended.body}</div>
    </div>
  );
}

export function isSuspended(memberships: Membership[]): boolean {
  return memberships[0]?.license_status === "suspended";
}
