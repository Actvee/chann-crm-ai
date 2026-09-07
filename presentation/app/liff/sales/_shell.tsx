"use client";

import { ReactNode } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

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
      notice={<SuspendedNotice memberships={session.memberships} />}
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
