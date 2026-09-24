"use client";

import { usePathname } from "next/navigation";
import { ReactNode } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import { NotificationBell } from "@/lib/NotificationBell";

import { currentKey, lockedBy, navGroups } from "../_nav-model";
import { PlanLocked } from "../_plan";
import { ShopSwitcher } from "../_shop-switcher";
import { SuspendedNotice } from "../_suspended";
import { AppShell } from "./_components";
import type { SalesSessionHandle } from "./_session";

/**
 * AppShell with the two things every Sales page owes the person using it
 * (review C4/C5, 6 Sep 2026): the read-only notice when the shop is
 * suspended, and the server-persisted shop switcher when they belong to
 * more than one. Both come from the session, so a page cannot forget one.
 */
export function SalesShell({
  session,
  title,
  back,
  liffId,
  status,
  statusTone,
  onSdkError,
  wide,
  guideHref,
  children,
}: {
  session: SalesSessionHandle;
  title: string;
  back?: string | null;
  liffId: string;
  status?: string;
  statusTone?: "ok" | "error";
  onSdkError: () => void;
  wide?: boolean;
  guideHref?: string | null;
  children: ReactNode;
}) {
  const { t } = useLanguage();
  // Round 21D (spec §5.4, §8.2): the page this path belongs to, and the
  // plan feature that locks it on this shop's plan, if any.
  const pathname = usePathname() ?? "";
  const entries = navGroups(t, "sales").flatMap((group) => group.entries);
  const here = entries.find((entry) => entry.key === currentKey(entries, pathname));
  // "part" pages (teams) lock their own part; the rest stays usable.
  const locked = here && here.lockMode !== "part" ? lockedBy(here, session.plan) : null;
  const canUpgrade = session.isOwner || session.permissions.has("setting.manage");
  const lockPanel = locked ? (
    <PlanLocked feature={locked} plan={session.plan} canUpgrade={canUpgrade} contact={session.salesContact} />
  ) : null;
  // Until /me answers, a page the plan may replace is not drawn — so it
  // never flashes open and then turns into the locked panel. The status
  // line above still says it is opening.
  const waiting = !session.ready && Boolean(here?.feature) && (here?.lockMode ?? "page") === "page";
  return (
    <AppShell
      title={title}
      back={back}
      liffId={liffId}
      onReady={() => void session.initialize()}
      onSdkError={onSdkError}
      status={status}
      statusTone={statusTone}
      wide={wide}
      guideHref={guideHref}
      // The rail draws only what this person's /me says they may open.
      permissions={session.permissions}
      isOwner={session.isOwner}
      heldKeys={session.heldKeys}
      plan={session.plan}
      meAnswered={session.meAnswered}
      notice={<SuspendedNotice memberships={session.memberships} />}
      // One bell, in the bar, on every Sales page. Before this it was placed
      // by hand on four pages and absent from the other fifteen, so whether
      // a person saw their notifications depended on the screen they were
      // standing on (owner, 18 ก.ย. 2569).
      tools={
        session.token && session.licenseId ? (
          <NotificationBell idToken={session.token} licenseId={session.licenseId} />
        ) : undefined
      }
    >
      {session.memberships.length > 1 && (
        <ShopSwitcher
          token={session.token}
          audience="sales"
          shops={session.memberships}
          current={session.licenseId}
          label={t.dashboard.customer.shopSwitch}
          onSwitched={() => void session.switchShop()}
        />
      )}
      {/* Round 21D: a page opened directly (bookmark, deep link) that the
          plan locks shows the locked panel instead of itself — or above
          itself for a read-only page (spec §5.4, §3.4). */}
      {waiting ? null : lockPanel && here?.lockMode !== "notice" ? lockPanel : <>{lockPanel}{children}</>}
    </AppShell>
  );
}
