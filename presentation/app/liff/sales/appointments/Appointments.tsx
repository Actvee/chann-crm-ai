"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Count, Empty } from "../_components";
import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { ConfirmDialog, useConfirm } from "../../_confirm";
import { ListFilters, matchesQuery } from "../../_filters";

type FollowUp = {
  id: string;
  entity_type: string;
  entity_id: string;
  due_date: string;
  due_time?: string | null;
  status: string;
  notes?: string | null;
};

type Named = { id: string; code: string; name: string };

/**
 * Every appointment the shop has, on one page.
 *
 * They already existed — on each customer's and each deal's own page, so
 * seeing the week meant opening records one at a time, and the owner
 * asked whether the status could be changed at all (17 ก.ย. 2569). It
 * could; it was just three taps inside a record nobody thought to open.
 *
 * Grouped by when, because that is the question being asked: what is
 * overdue, what is today, what is coming. Each row names the customer or
 * the deal it belongs to and links there, so the page is a way in rather
 * than a dead end.
 */
export default function Appointments({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const failureText = useFailureText();
  const copy = t.dashboard.appointments;
  const [rows, setRows] = useState<FollowUp[]>([]);
  const [names, setNames] = useState<Record<string, Named>>({});
  const [query, setQuery] = useState("");
  // A real status, not a checkbox beside a select that did nothing. It
  // starts at "pending" so the default view is unchanged: what is still
  // waiting on you, with what is finished a choice away.
  const [statusFilter, setStatusFilter] = useState("pending");
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [busy, setBusy] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, ready } = session;

  const load = useCallback(async () => {
    if (!ready || !licenseId) return;
    const headers = proxyHeaders(token, licenseId);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/follow-ups`,
        { headers },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const all = (await response.json()) as FollowUp[];
      setRows(all);
      // One read each for the two kinds a follow-up can hang off, so a row
      // can say whose appointment it is instead of showing a bare id.
      const [customers, deals] = await Promise.all([
        fetch(`/api/phase2/licenses/${licenseId}/customers`, { headers }).then((r) =>
          r.ok ? r.json() : [],
        ),
        fetch(`/api/phase2/licenses/${licenseId}/deals`, { headers }).then((r) =>
          r.ok ? r.json() : [],
        ),
      ]);
      const table: Record<string, Named> = {};
      for (const c of customers as Record<string, string>[]) {
        table[String(c.id)] = {
          id: String(c.id),
          code: String(c.customer_id ?? ""),
          name: [c.first_name, c.last_name].filter(Boolean).join(" ").trim(),
        };
      }
      for (const d of deals as Record<string, string>[]) {
        table[String(d.id)] = {
          id: String(d.id),
          code: String(d.deal_id ?? ""),
          name: String(d.deal_id ?? ""),
        };
      }
      setNames(table);
      say("");
    } catch {
      say(t.common.error, "error");
    }
  }, [ready, licenseId, token, failureText, say, t.common.error]);

  useEffect(() => {
    void load();
  }, [load]);

  async function setStatusOf(row: FollowUp, next: "completed" | "cancelled") {
    if (next === "cancelled") {
      const ok = await ask({
        action: copy.cancelAction,
        target: whoFor(row),
        code: whenOf(row),
        affects: row.notes ? [row.notes] : undefined,
        reversible: copy.cancelKeeps,
        confirmLabel: copy.cancelAction,
      });
      if (!ok) return;
    }
    setBusy(row.id);
    try {
      const response = await fetch(
        `/api/phase2/follow-ups/${row.id}/status?status_value=${next}`,
        { method: "PATCH", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      await load();
      say(
        (next === "completed" ? copy.markedDone : copy.markedCancelled)
          .replace("{who}", whoFor(row))
          .replace("{when}", whenOf(row)),
        "ok",
      );
    } catch {
      say(t.common.error, "error");
    } finally {
      setBusy("");
    }
  }

  function whoFor(row: FollowUp): string {
    const named = names[String(row.entity_id)];
    return named?.name || named?.code || copy.unknownRecord;
  }

  function whenOf(row: FollowUp): string {
    const time = row.due_time ? ` ${String(row.due_time).slice(0, 5)}` : "";
    return `${row.due_date}${time}`;
  }

  function hrefFor(row: FollowUp): string | null {
    const named = names[String(row.entity_id)];
    if (!named) return null;
    return row.entity_type === "deal"
      ? `/liff/sales/deals/${named.id}`
      : `/liff/sales/customers/${named.id}`;
  }

  const today = new Date().toISOString().slice(0, 10);
  const visible = rows.filter(
    (row) =>
      (statusFilter === "" || row.status === statusFilter) &&
      matchesQuery(query, [whoFor(row), row.notes, row.due_date]),
  );
  const groups: { key: string; title: string; rows: FollowUp[] }[] = [
    {
      key: "overdue",
      title: copy.overdue,
      rows: visible.filter((r) => r.status === "pending" && r.due_date < today),
    },
    {
      key: "today",
      title: copy.today,
      rows: visible.filter((r) => r.status === "pending" && r.due_date === today),
    },
    {
      key: "ahead",
      title: copy.ahead,
      rows: visible.filter((r) => r.status === "pending" && r.due_date > today),
    },
    {
      key: "done",
      title: copy.settled,
      rows: visible.filter((r) => r.status !== "pending"),
    },
  ].filter((g) => g.rows.length > 0);

  return (
    <SalesShell
      session={session}
      title={copy.title}
      back="/liff/sales"
      liffId={liffId}
      status={status}
      statusTone={tone}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
    >
      <div className="page-head">
        {/* `statuses={[]}` is what was here, and an empty array is truthy:
            the select rendered with "ทั้งหมด" as its only option and an
            onStatus that did nothing, beside a checkbox that did the
            filtering — two status controls, one of them dead (owner,
            18 ก.ย. 2569). One control now, the same as every other list. */}
        <ListFilters
          query={query}
          onQuery={setQuery}
          placeholder={copy.searchHint}
          statuses={[
            { value: "pending", label: copy.statusPending },
            { value: "completed", label: copy.statusCompleted },
            { value: "cancelled", label: copy.statusCancelled },
          ]}
          status={statusFilter}
          onStatus={setStatusFilter}
        />
      </div>

      {groups.length === 0 ? (
        <Empty message={query ? t.dashboard.noMatch : copy.none} />
      ) : (
        groups.map((group) => (
          <section key={group.key} className="pa-section">
            <h2 className="section-head">
              {group.title} <Count shown={group.rows.length} total={group.rows.length} />
            </h2>
            <ul className="list">
              {group.rows.map((row) => {
                const href = hrefFor(row);
                return (
                  <li key={row.id} className="card" data-stage={row.status}>
                    {/* The whole row opens the record, with the chevron the
                        other lists use — every other page in the dashboard
                        is tapped on the row itself, and this one alone had
                        a button labelled "เปิดระเบียน", which is the
                        database's word for it, not a person's (owner,
                        18 ก.ย. 2569). The action buttons stay OUTSIDE the
                        link: a button inside a link is reached twice by a
                        keyboard and reads as one control to a screen
                        reader. */}
                    {href ? (
                      <Link className="row-link" href={href}>
                        <div className="row-body">
                          <div className="card-title">
                            {whenOf(row)} · {whoFor(row)}
                          </div>
                          {row.notes && <div className="card-meta">{row.notes}</div>}
                        </div>
                      </Link>
                    ) : (
                      <div className="row-body">
                        <div className="card-title">
                          {whenOf(row)} · {whoFor(row)}
                        </div>
                        {row.notes && <div className="card-meta">{row.notes}</div>}
                        <div className="card-meta">{copy.recordGone}</div>
                      </div>
                    )}
                    {row.status === "pending" && (
                      <div className="card-actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="primary"
                          disabled={busy !== ""}
                          onClick={() => void setStatusOf(row, "completed")}
                        >
                          {copy.markDone}
                        </button>
                        <button
                          type="button"
                          className="btn"
                          disabled={busy !== ""}
                          onClick={() => void setStatusOf(row, "cancelled")}
                        >
                          {copy.markCancelled}
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={Boolean(busy)} />
    </SalesShell>
  );
}
