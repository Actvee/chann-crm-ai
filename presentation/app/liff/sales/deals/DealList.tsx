"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { FieldRow } from "../../_field-row";
import { InlineCreateForm } from "../../_inline-create";
import {
  ListControls, byNewest, byOldest, shortDate, useListControls,
} from "../../_list-controls";

import { useFailureText, useFormatters } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";

type Deal = {
  id: string;
  deal_id: string;
  stage: string;
  notes?: string | null;
  products?: { qty?: number; quoted_unit_price?: string | number }[];
  created_at?: string | null;
  expected_close_date?: string | null;
  amount?: string | number | null;
  currency?: string | null;
};

/** The deal's own amount when the salesperson gave one; otherwise the line items. */
function dealValue(deal: Deal): number {
  if (deal.amount != null && deal.amount !== "" && Number(deal.amount) > 0) return Number(deal.amount);
  return (deal.products ?? []).reduce(
    (sum, p) => sum + Number(p.qty ?? 0) * Number(p.quoted_unit_price ?? 0), 0,
  );
}

// Only the moves the Phase 9 state machine actually permits. Offering "won"
// on an already-won deal would put a button in front of someone whose only
// possible outcome is an error.
const NEXT_STAGES: Record<string, string[]> = {
  new: ["proposed", "lost"],
  proposed: ["won", "lost"],
  // won/lost → new is the reopen (9.6); offered only with deal.reopen.
  won: [],
  lost: [],
};
const REOPEN_STAGES = ["won", "lost"];
function nextStages(stage: string, canReopen: boolean): string[] {
  const base = NEXT_STAGES[stage] ?? [];
  return canReopen && REOPEN_STAGES.includes(stage) ? [...base, "new"] : base;
}

export default function DealList({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const s = useSalesText();
  const { money } = useFormatters();
  const failureText = useFailureText();
  const stageLabel = (stage: string) =>
    (t.deal.stage as Record<string, string>)[stage] ?? stage;
  const [deals, setDeals] = useState<Deal[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [contacts, setContacts] = useState<
    { id: string; name: string; keywords?: string }[]
  >([]);
  const [busyId, setBusyId] = useState("");
  const [openOnly, setOpenOnly] = useState(false);
  // Why a deal was lost, asked inline (review C20): window.prompt is
  // silently a no-op inside some LINE webviews, which made "ปิดไม่สำเร็จ"
  // a button that did nothing.
  const [losing, setLosing] = useState<Deal | null>(null);
  const [lostReason, setLostReason] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

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
  }, [licenseId, say, t, token]);

  const loadContacts = useCallback(async () => {
    if (!token || !licenseId || !permissions.has("deal.create")) return;
    // A deal needs a customer, so the picker is filled up front rather
    // than asking anyone to remember a code.
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/customers`,
      { headers: proxyHeaders(token, licenseId) },
    );
    if (!response.ok) {
      // Without customers the create-deal form silently does not
      // appear, which reads as "you cannot create deals". Say why.
      say(`${t.dashboard.loadFailed} (${response.status})`, "error");
      return;
    }
    const rows = (await response.json()) as {
      id: string; first_name?: string; last_name?: string;
      customer_id?: string; stage?: string; phone?: string | null;
    }[];
    setContacts(
      rows
        // Contacts only: a lead has not agreed to anything, and
        // opening a deal on one skips the step that says they did.
        .filter((row) => row.stage !== "lead")
        .map((row) => ({
          id: row.id,
          name: `${row.first_name ?? ""} ${row.last_name ?? ""}`.trim()
            || (row.customer_id ?? row.id),
          // Searchable by the number off a missed call and by the code
          // (review C14): the picker matched on the name alone.
          keywords: [row.phone, row.customer_id].filter(Boolean).join(" "),
        })),
    );
  }, [licenseId, permissions, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
    void loadContacts().catch(() => undefined);
  }, [session.ready, load, loadContacts, say, t]);

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
        const why =
          body && typeof body === "object" && body.error === "duplicate"
            ? t.dashboard.deals.alreadyOpen.replace(
                "{code}", String(body.existing_code ?? ""),
              )
            : await failureText(response);
        say(why, "error");
        throw new Error(why);
      }
      await load();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function setStage(deal: Deal, stage: string, reason?: string) {
    setBusyId(deal.id);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/deals/${deal.id}/stage`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            stage,
            allow_reopen: stage === "new" && permissions.has("deal.reopen"),
            lost_reason: reason?.trim() || undefined,
          }),
        },
      );
      if (!response.ok) {
        say(
          response.status === 403 ? t.dashboard.deals.stageDenied : await failureText(response),
          "error",
        );
        return;
      }
      say(`${deal.deal_id} → ${stageLabel(stage)}`, "ok");
      setLosing(null);
      setLostReason("");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  function askOrSet(deal: Deal, stage: string) {
    if (stage === "lost") {
      setLosing(deal);
      setLostReason("");
      return;
    }
    void setStage(deal, stage);
  }

  const stageFiltered = openOnly
    ? // Filtering on the two terminal stages rather than listing the open
      // ones means a stage added later counts as open by default, which is
      // the safer direction to be wrong in for a work queue.
      deals.filter((deal) => !["won", "lost"].includes(deal.stage))
    : deals;

  const sorts = [
    { key: "newest", label: t.dashboard.list.newest, compare: byNewest<Deal> },
    { key: "oldest", label: t.dashboard.list.oldest, compare: byOldest<Deal> },
    {
      key: "value",
      label: t.dashboard.list.highestValue,
      compare: (a: Deal, b: Deal) => dealValue(b) - dealValue(a),
    },
    {
      key: "closing",
      label: t.dashboard.list.closingSoonest,
      // Undated deals sink to the bottom rather than sorting as the
      // earliest — an empty string compares before any real date.
      compare: (a: Deal, b: Deal) =>
        (a.expected_close_date ?? "9999").localeCompare(b.expected_close_date ?? "9999"),
    },
  ];
  const controls = useListControls(stageFiltered, sorts, "newest");
  const visible = controls.visible;
  const can = (key: string) => !session.suspended && permissions.has(key);
  // The stage buttons need deal.update (C7); the list offered them to
  // everyone who could read.
  const moves = (deal: Deal) => (can("deal.update") ? nextStages(deal.stage, can("deal.reopen")) : []);

  return (
    <SalesShell
      session={session}
      title={t.deal.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
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

      <ListControls
        sorts={sorts}
        sortKey={controls.sortKey}
        onSort={controls.setSortKey}
        from={controls.from}
        to={controls.to}
        onFrom={controls.setFrom}
        onTo={controls.setTo}
      />

      <Count shown={visible.length} total={deals.length} />

      {can("deal.create") && contacts.length > 0 && (
        <InlineCreateForm
          title={t.dashboard.deals.add}
          busy={busy}
          fields={[
            {
              name: "contact_id",
              label: t.dashboard.fields.customer,
              required: true,
              type: "select",
              searchHint: t.dashboard.customers.searchHint,
              options: contacts.map((c) => ({
                value: c.id, label: c.name, keywords: c.keywords,
              })),
            },
            { name: "notes", label: t.dashboard.fields.notes },
          ]}
          onSubmit={createDeal}
        />
      )}

      {losing && (
        <section className="section" style={{ marginBottom: 16 }}>
          <div className="section-head">
            <h2>{s.deals.lostReasonTitle} — <span className="code">{losing.deal_id}</span></h2>
          </div>
          <dl className="fields">
            <FieldRow label={t.dashboard.deals.lostReason}>
              {(id) => (
                <textarea
                  id={id}
                  rows={2}
                  value={lostReason}
                  placeholder={s.deals.lostReasonHint}
                  onChange={(event) => setLostReason(event.target.value)}
                />
              )}
            </FieldRow>
            <div className="actions">
              <button
                type="button"
                className="btn"
                data-variant="quiet"
                onClick={() => setLosing(null)}
                disabled={busyId === losing.id}
              >
                {t.common.cancel}
              </button>
              <button
                type="button"
                className="btn"
                data-variant="danger"
                onClick={() => void setStage(losing, "lost", lostReason)}
                disabled={busyId === losing.id}
              >
                {busyId === losing.id ? t.dashboard.saving : s.deals.confirmLost}
              </button>
            </div>
          </dl>
        </section>
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
                {dealValue(deal) > 0
                  ? ` · ${t.dashboard.deals.amount} ${money(dealValue(deal), 0)} ${deal.currency ?? "THB"}`
                  : ""}
                {deal.expected_close_date
                  ? ` · ${t.dashboard.deals.expectedClose} ${shortDate(deal.expected_close_date, locale)}`
                  : ""}
                {deal.notes ? ` · ${deal.notes}` : ""}
              </div>
              {/* Created time is a system field on every CRM record, and
                  the thing a date filter is filtering on — a list that
                  can be filtered by a date it does not show is a puzzle. */}
              <div className="card-meta" style={{ fontSize: 12, color: "var(--ink-faint)" }}>
                {shortDate(deal.created_at, locale)}
              </div>
              </Link>
              {moves(deal).length ? (
                <div className="card-actions">
                  {moves(deal).map((stage) => (
                    <button
                      key={stage}
                      type="button"
                      className="btn"
                      data-variant={stage === "won" ? "primary" : undefined}
                      onClick={() => askOrSet(deal, stage)}
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
    </SalesShell>
  );
}
