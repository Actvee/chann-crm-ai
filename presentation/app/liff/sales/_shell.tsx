"use client";

import { ReactNode } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import { NotificationBell } from "@/lib/NotificationBell";

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
      {children}
    </AppShell>
  );
}
