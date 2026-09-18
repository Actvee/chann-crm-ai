"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Count, Empty } from "../_components";
import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { ListFilters, matchesQuery } from "../../_filters";

type Row = {
  id: string;
  entity_type: string;
  entity_id: string;
  actor_type: string;
  actor_id?: string | null;
  actor_name?: string | null;
  action: string;
  field_changes?: Record<string, { old?: unknown; new?: unknown }> | null;
  created_at: string;
};

/**
 * Who changed what in this shop.
 *
 * The table has been written to since Phase 2 and "ดูประวัติการใช้งาน" has
 * been a permission an owner holds for just as long — and it opened
 * nothing. The Data route had no caller; the Application route was added
 * on 6 ก.ย. 2569 because of that, and then had no caller either, so the
 * gap simply moved up a tier (audit, 17 ก.ย. 2569). This is the screen.
 *
 * A shop reads this to settle one question — "ใครลบลูกค้ารายนี้" — so the
 * row leads with the person and the day, and the field-by-field detail is
 * folded away behind it rather than filling the page with ids.
 */
export default function AuditTrail({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const failureText = useFailureText();
  const copy = t.dashboard.history;
  const [rows, setRows] = useState<Row[]>([]);
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string>("");
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind_?: "ok" | "error") => {
    setStatus(message);
    setTone(kind_);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, ready } = session;

  const load = useCallback(async () => {
    if (!ready || !licenseId) return;
    try {
      // The "?" stays in the literal: check-parity reads these URLs as
      // text, and a template placeholder glued to the last path segment
      // hides the endpoint from it entirely.
      const params = kind
        ? `entity_type=${encodeURIComponent(kind)}&limit=200`
        : "limit=200";
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/audit-log?${params}`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      setRows((await response.json()) as Row[]);
      say("");
    } catch {
      say(t.common.error, "error");
    }
  }, [ready, licenseId, token, kind, failureText, say, t.common.error]);

  useEffect(() => {
    void load();
  }, [load]);

  const kinds = [
    "customer",
    "deal",
    "quote",
    "ticket",
    "product",
    "warranty",
    "license_member",
    "license_invite",
    "license_role",
    "license_setting",
  ];
  const kindLabel = (value: string) =>
    (copy.kinds as Record<string, string>)[value] ?? value;
  const actionLabel = (value: string) =>
    (copy.actions as Record<string, string>)[value] ?? value;
  const whoOf = (row: Row) =>
    row.actor_name || (row.actor_type === "user" ? copy.someone : copy.system);

  const visible = rows.filter((row) =>
    matchesQuery(query, [whoOf(row), kindLabel(row.entity_type), actionLabel(row.action)]),
  );

  // Grouped by day, newest first: the rows arrive in that order, so the
  // grouping only has to notice where the date changes.
  const days: { day: string; rows: Row[] }[] = [];
  for (const row of visible) {
    const day = String(row.created_at).slice(0, 10);
    const last = days[days.length - 1];
    if (last && last.day === day) last.rows.push(row);
    else days.push({ day, rows: [row] });
  }

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
      <p className="page-intro">{copy.intro}</p>
      <div className="page-head">
        <ListFilters
          query={query}
          onQuery={setQuery}
          placeholder={copy.searchHint}
          status={kind}
          statuses={kinds.map((value) => ({ value, label: kindLabel(value) }))}
          onStatus={setKind}
          statusLabel={copy.kind}
          allLabel={copy.allKinds}
        />
      </div>

      {days.length === 0 ? (
        <Empty message={query || kind ? t.dashboard.noMatch : copy.none} />
      ) : (
        days.map((group) => (
          <section key={group.day} className="pa-section">
            <h2 className="section-head">
              {group.day} <Count shown={group.rows.length} total={group.rows.length} />
            </h2>
            <ul className="list">
              {group.rows.map((row) => {
                const changes = Object.entries(row.field_changes ?? {});
                const showing = open === row.id;
                return (
                  <li key={row.id} className="card">
                    <div className="card-title">
                      {String(row.created_at).slice(11, 16)} · {whoOf(row)}{" "}
                      {actionLabel(row.action)}
                      {kindLabel(row.entity_type)}
                    </div>
                    {changes.length > 0 && (
                      <div className="card-actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          aria-expanded={showing}
                          onClick={() => setOpen(showing ? "" : row.id)}
                        >
                          {showing
                            ? copy.hideChanges
                            : copy.showChanges.replace("{n}", String(changes.length))}
                        </button>
                      </div>
                    )}
                    {showing && (
                      <dl className="card-meta">
                        {changes.map(([field, change]) => (
                          <div key={field}>
                            <dt>{field}</dt>
                            <dd>
                              {String(change?.old ?? copy.blank)} →{" "}
                              {String(change?.new ?? copy.blank)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
    </SalesShell>
  );
}
