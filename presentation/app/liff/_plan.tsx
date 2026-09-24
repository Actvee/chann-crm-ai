"use client";

import { ReactNode, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { openExternal, proxyHeaders } from "./_shared";

/**
 * Round 21D — the shop's sales plan on the dashboard (spec §5.4, §8).
 *
 * The plan comes from /me/permissions (the Application's PlanView), so no
 * page fetches it on its own; only the plan card and the members page ask
 * for usage (GET …/plan). The API refuses a locked feature regardless —
 * this file decides what is DRAWN.
 *
 * ui-ux-pro-max (owner rule, 20 Sep 2026): a reason is visible text under
 * the control, never only a tooltip (`disabled-states`,
 * `input-helper-text`); the lock is a glyph AND words, not colour alone
 * (`color-not-only`); the reason colour is --ink-soft, ≥ 4.5:1 on white
 * (`color-contrast`); the contact button is a 44px .btn
 * (`touch-target-size`).
 */
export type PlanInfo = {
  code: string;
  label: string;
  features: string[];
  locked: Record<string, string>;
  limits: { members: number | null; ai_reports_per_month: number };
  known?: boolean;
};

/** `{label, url}` from CHANN_SALES_CONTACT, for the owner / setting.manage
 *  only. The url may be EMPTY (a non-https value is dropped by the
 *  Application) — then no button is drawn, only words. */
type ContactInfo = {
  label: string;
  url: string;
};
export type SalesContact = ContactInfo | null;

export type PlanUsage = {
  members?: number;
  members_limit?: number | null;
  ai_reports_used?: number;
  ai_reports_allowance?: number;
  ai_reports_month?: string;
};

export const PLAN_LABELS: Record<string, string> = {
  starter: "Starter", pro: "Pro", enterprise: "Enterprise", enterprise_plus: "Enterprise Plus",
};

const ORDERED_FEATURES = [
  "feature.customer_line_link", "feature.live_chat", "feature.service", "feature.warranty",
  "feature.custom_documents", "feature.custom_roles", "feature.multi_level_approval",
  "feature.external_api",
] as const;

/** Does the plan have this key? An unanswered /me (no plan) draws
 *  everything — the same direction fetchPermissions fails in; the server
 *  still refuses what the plan locks. Mirrors PlanView.has. */
export function planHas(plan: PlanInfo | null | undefined, key: string): boolean {
  if (!plan) return true;
  if (key === "limit.members") return true;
  if (key === "quota.ai_reports_per_month") return !(key in (plan.locked ?? {}));
  return (plan.features ?? []).includes(key);
}

/** "Pro" — the lowest plan that has the key, as the server computed it. */
export function minPlanLabel(plan: PlanInfo | null | undefined, key: string): string {
  const code = plan?.locked?.[key] ?? "pro";
  return PLAN_LABELS[code] ?? "Pro";
}

export const LOCK_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
);

const CHECK_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);

/** A disabled control's reason, as text under it (never only title=). */
export function PlanReason({ children }: { children: ReactNode }) {
  return <p className="plan-reason">{children}</p>;
}

/** The upgrade button — only when CHANN_SALES_CONTACT has a URL (owner
 *  decision Q5); otherwise the sentence that says who to ask, with the
 *  contact's label when there is one (an empty url still names the OA). */
export function UpgradeContact({ contact, primary = true }: { contact: SalesContact; primary?: boolean }) {
  const { t } = useLanguage();
  if (contact?.url) {
    const url = contact.url;
    return (
      <button type="button" className="btn" data-variant={primary ? "primary" : undefined}
              onClick={() => openExternal(url)}>
        {t.dashboard.plan.contactButton}
      </button>
    );
  }
  const label = contact?.label ? ` · ${contact.label}` : "";
  return <PlanReason>{`${t.dashboard.plan.contactNone}${label}`}</PlanReason>;
}

/** Spec §8.2 — the page a locked feature shows instead of itself (or above
 *  itself, for the read-only pages; in place of one part, for teams). */
export function PlanLocked({
  feature, plan, canUpgrade, contact,
}: { feature: string; plan: PlanInfo | null; canUpgrade: boolean; contact: SalesContact }) {
  const { t } = useLanguage();
  const p = t.dashboard.plan;
  const f = (p.features as Record<string, { label: string; desc: string }>)[feature];
  return (
    <section className="card plan-locked">
      <span className="plan-locked-icon">{LOCK_ICON}</span>
      <h2>{p.lockedTitle.replace("{min_plan}", minPlanLabel(plan, feature))}</h2>
      <p>{f ? `${f.label}: ${f.desc}` : feature}</p>
      <p className="card-meta">{p.lockedShop.replace("{plan}", plan?.label ?? "")}</p>
      {canUpgrade ? <UpgradeContact contact={contact} /> : <PlanReason>{p.askOwner}</PlanReason>}
    </section>
  );
}

/** Spec §8.4 — the plan card on the company page (setting.manage). The
 *  space is reserved while it loads (`content-jumping`), so the
 *  subscription section below does not jump down when it arrives. */
export function PlanCard({
  token, licenseId, canUpgrade, contact,
}: { token: string; licenseId: string; canUpgrade: boolean; contact: SalesContact }) {
  const { t } = useLanguage();
  const p = t.dashboard.plan;
  const [plan, setPlan] = useState<PlanInfo | null>(null);
  const [usage, setUsage] = useState<PlanUsage>({});
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!token || !licenseId) return;
    let live = true;
    void (async () => {
      try {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/plan`, {
          headers: proxyHeaders(token, licenseId),
        });
        if (!response.ok) {
          if (live) setFailed(true);
          return;
        }
        const body = (await response.json()) as { plan: PlanInfo; usage: PlanUsage };
        if (live) {
          setPlan(body.plan);
          setUsage(body.usage ?? {});
        }
      } catch {
        // The card is information; a failed read leaves the page usable.
        if (live) setFailed(true);
      }
    })();
    return () => {
      live = false;
    };
  }, [token, licenseId]);
  if (failed) return null;
  if (!plan) {
    return (
      <section className="section plan-card plan-card-loading" aria-busy="true">
        <p className="basic-value skeleton" aria-hidden="true">&nbsp;</p>
      </section>
    );
  }
  const n = usage.members ?? 0;
  const users = plan.limits.members == null
    ? p.usersUnlimited.replace("{n}", String(n))
    : p.users.replace("{n}", String(n)).replace("{limit}", String(plan.limits.members));
  // The usage row carries the allowance with an admin top-up applied;
  // the plan's own number is the fallback.
  const allowance = usage.ai_reports_allowance ?? plan.limits.ai_reports_per_month;
  const features = p.features as Record<string, { label: string; desc: string }>;
  return (
    <section className="section plan-card">
      <div className="section-head">
        <h2>{p.cardTitle.replace("{plan}", plan.label)}</h2>
      </div>
      <p className="card-meta">{users}</p>
      <p className="card-meta">
        {planHas(plan, "quota.ai_reports_per_month")
          ? p.aiCredits.replace("{used}", String(usage.ai_reports_used ?? 0))
              .replace("{allowance}", String(allowance))
          // Not aiLocked: that line points at basic reports "above", which
          // are on the AI reports page, not here (review M3).
          : `${features["quota.ai_reports_per_month"]?.label ?? ""} · ${p.fromPlan.replace("{plan}", minPlanLabel(plan, "quota.ai_reports_per_month"))}`}
      </p>
      <ul className="plan-features">
        {ORDERED_FEATURES.map((key) => {
          const on = planHas(plan, key);
          return (
            <li key={key} data-on={on ? "true" : "false"}>
              <span className="plan-feature-icon">{on ? CHECK_ICON : LOCK_ICON}</span>
              <span>{features[key]?.label ?? key}</span>
              {!on && <span className="plan-reason">{p.fromPlan.replace("{plan}", minPlanLabel(plan, key))}</span>}
              <span className="sr-only">{on ? p.included : p.notIncluded}</span>
            </li>
          );
        })}
      </ul>
      {/* Secondary here: the company page's primary is its own Save
          (`primary-action`, one primary per screen). */}
      {canUpgrade && <UpgradeContact contact={contact} primary={false} />}
    </section>
  );
}
