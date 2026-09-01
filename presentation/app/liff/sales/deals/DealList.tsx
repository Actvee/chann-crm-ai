"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell, Badge, CompanyPicker, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { InlineCreateForm } from "../../_inline-create";

import { Membership, fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

type Deal = {
  id: string;
  deal_id: string;
  stage: string;
  notes?: string | null;
  products?: unknown[];
};

// Only the moves the Phase 9 state machine actually permits. Offering "won"
// on an already-won deal would put a button in front of someone whose only
// possible outcome is an error.
const NEXT_STAGES: Record<string, string[]> = {
  new: ["proposed", "lost"],
  proposed: ["won", "lost"],
  won: [],
  lost: [],
};

export default function DealList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const stageLabel = (stage: string) =>
    (t.deal.stage as Record<string, string>)[stage] ?? stage;
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [deals, setDeals] = useState<Deal[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [contacts, setContacts] = useState<{ id: string; name: string }[]>([]);
  const [busyId, setBusyId] = useState("");
  const [openOnly, setOpenOnly] = useState(false);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/deals`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    setDeals((await response.json()) as Deal[]);
    say("");
  }, [licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [licenseId, load, say, token]);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      setToken(session.token);
      setMemberships(session.memberships);
      setLicenseId(session.memberships[0]?.license_id ?? "");
      if (!session.memberships.length) say(t.liff.noCompany, "error");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say]);

  async function createDeal(values: Record<string, string>) {
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/deals`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify(values),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        const body = detail.detail;
        // One open deal per customer: saying which one beats "conflict".
        say(
          body && typeof body === "object" && body.error === "duplicate"
            ? t.dashboard.deals.alreadyOpen.replace(
                "{code}", String(body.existing_code ?? ""),
              )
            : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      await load();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function setStage(deal: Deal, stage: string) {
    setBusyId(deal.id);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/deals/${deal.id}/stage`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({ stage, allow_reopen: false }),
        },
      );
      if (!response.ok) {
        say(
          response.status === 403
            ? t.dashboard.deals.stageDenied
            : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      say(`${deal.deal_id} → ${stageLabel(stage)}`, "ok");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  const visible = openOnly
    ? // Filtering on the two terminal stages rather than listing the open
      // ones means a stage added later counts as open by default, which is
      // the safer direction to be wrong in for a work queue.
      deals.filter((deal) => !["won", "lost"].includes(deal.stage))
    : deals;

  return (
    <AppShell
      title={t.deal.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <button
          type="button"
          className="btn"
          data-variant={openOnly ? undefined : "primary"}
          onClick={() => setOpenOnly(false)}
        >
          {t.dashboard.deals.all}
        </button>
        <button
          type="button"
          className="btn"
          data-variant={openOnly ? "primary" : undefined}
          onClick={() => setOpenOnly(true)}
        >
          {t.dashboard.deals.openOnly}
        </button>
      </div>

      <Count shown={visible.length} total={deals.length} />

      {permissions.has("deal.create") && contacts.length > 0 && (
        <InlineCreateForm
          title={t.dashboard.deals.add}
          busy={busy}
          fields={[
            {
              name: "contact_id",
              label: t.dashboard.fields.customer,
              required: true,
              type: "select",
              options: contacts.map((c) => ({ value: c.id, label: c.name })),
            },
            { name: "notes", label: t.dashboard.fields.notes },
          ]}
          onSubmit={createDeal}
        />
      )}

      {visible.length === 0 ? (
        <Empty
          message={
            deals.length === 0
              ? t.dashboard.deals.empty
              : t.dashboard.deals.noOpen
          }
        />
      ) : (
        <ul className="list">
          {visible.map((deal) => (
            <li key={deal.id} className="card" data-stage={deal.stage}>
              <Link
                href={`/liff/sales/deals/${deal.id}`}
                style={{ textDecoration: "none", color: "inherit" }}
              >
              <div className="card-title">
                <span className="code">{deal.deal_id}</span>
                <Badge stage={deal.stage} label={stageLabel(deal.stage)} />
              </div>
              <div className="card-meta">
                {(deal.products?.length ?? 0) > 0
                  ? t.dashboard.deals.lineItems.replace(
                      "{count}", String(deal.products?.length ?? 0),
                    )
                  : t.dashboard.deals.noLineItems}
                {deal.notes ? ` · ${deal.notes}` : ""}
              </div>
              </Link>
              {NEXT_STAGES[deal.stage]?.length ? (
                <div className="card-actions">
                  {NEXT_STAGES[deal.stage].map((stage) => (
                    <button
                      key={stage}
                      type="button"
                      className="btn"
                      data-variant={stage === "won" ? "primary" : undefined}
                      onClick={() => void setStage(deal, stage)}
                      disabled={busyId === deal.id}
                    >
                      {busyId === deal.id
                        ? t.dashboard.saving
                        : t.dashboard.deals.changeTo.replace("{stage}", stageLabel(stage))}
                    </button>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
