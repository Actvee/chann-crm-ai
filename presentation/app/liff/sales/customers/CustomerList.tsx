"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { BulkPaste } from "../_bulk-paste";
import { CsvImport } from "../_csv-import";
import { Badge, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { InlineCreateForm } from "../../_inline-create";
import {
  ListControls, byNewest, byOldest, useListControls,
} from "../../_list-controls";

import { useFailureText } from "../_format";
import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";

type Customer = {
  id: string;
  customer_id: string;
  first_name?: string | null;
  last_name?: string | null;
  stage: string;
  phone?: string | null;
  email?: string | null;
  created_at?: string | null;
};

function fullName(customer: Customer): string {
  return [customer.first_name, customer.last_name].filter(Boolean).join(" ") || "—";
}

export default function CustomerList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const s = useSalesText();
  const failureText = useFailureText();
  // Customer stages are only two, and both already have names in the
  // dictionary under their own sections.
  const stageLabel = (stage: string) =>
    stage === "contact" ? t.customer.title : t.customer.lead;
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [query, setQuery] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/customers`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    setCustomers((await response.json()) as Customer[]);
    say("");
  }, [licenseId, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [session.ready, load, say, t]);

  async function createCustomer(values: Record<string, string>) {
    if (values.phone && !/^\+?[\d\s\-().]+$/.test(values.phone)) {
      say(t.dashboard.customers.phoneLetters, "error");
      throw new Error("phone");
    }
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify(values),
        },
      );
      if (!response.ok) {
        // The API's own reason, in the reader's language — a duplicate
        // names the record that already holds the number, a 422 names
        // the field. Thrown so the form keeps what was typed (C3).
        const why = await failureText(response);
        say(
          response.status === 422 ? `${why} — ${s.customers.lastNameAndPhone}` : why,
          "error",
        );
        throw new Error(why);
      }
      await load();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  /** User review (4 Sep 2026): delete a lead — the platform's soft delete,
   *  confirmed first, behind customer.archive. */
  async function archive(customer: Customer) {
    if (!window.confirm(t.dashboard.customers.archiveConfirm.replace("{name}", fullName(customer)))) return;
    setBusyId(customer.id);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers/${customer.id}/archive`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(response.status === 403 ? t.dashboard.customers.archiveDenied : await failureText(response), "error");
        return;
      }
      setCustomers((rows) => rows.filter((row) => row.id !== customer.id));
      say(`${fullName(customer)} — ${t.dashboard.customers.archived}`, "ok");
    } catch {
      say(t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  async function promote(customer: Customer) {
    setBusyId(customer.id);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/customers/${customer.id}/promote`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(
          response.status === 403 ? t.dashboard.customers.promoteDenied : await failureText(response),
          "error",
        );
        return;
      }
      say(`${fullName(customer)} — ${t.dashboard.customers.promoted}`, "ok");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  // Filtered in the browser rather than by refetching: the tenant-scoped
  // list is already loaded and SMB-scale, so a round trip per keystroke
  // would add latency for no benefit.
  const needle = query.trim().toLowerCase();
  const searched = needle
    ? customers.filter((customer) =>
        [fullName(customer), customer.phone, customer.email, customer.customer_id]
          .map((value) => String(value ?? "").toLowerCase())
          .some((value) => value.includes(needle)),
      )
    : customers;

  const sorts = [
    { key: "newest", label: t.dashboard.list.newest, compare: byNewest<Customer> },
    { key: "oldest", label: t.dashboard.list.oldest, compare: byOldest<Customer> },
    {
      key: "name",
      label: t.dashboard.list.byName,
      compare: (a: Customer, b: Customer) => fullName(a).localeCompare(fullName(b), "th"),
    },
  ];
  const controls = useListControls(searched, sorts, "newest");
  const visible = controls.visible;
  // Every control is gated on the key its route actually checks (C7);
  // a suspended shop offers none of them (C4).
  const can = (key: string) => !session.suspended && permissions.has(key);

  return (
    <SalesShell
      session={session}
      title={t.customer.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <label className="field">
        <span>{t.dashboard.search}</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t.dashboard.customers.searchHint}
          type="search"
        />
      </label>

      <ListControls
        sorts={sorts}
        sortKey={controls.sortKey}
        onSort={controls.setSortKey}
        from={controls.from}
        to={controls.to}
        onFrom={controls.setFrom}
        onTo={controls.setTo}
      />

      <Count shown={visible.length} total={customers.length} />

      {can("customer.create") && (
        <BulkPaste token={token} licenseId={licenseId} onDone={() => void load()} />
      )}

      {can("customer.create") && (
        <CsvImport kind="customers" token={token} licenseId={licenseId} onDone={() => void load()} />
      )}

      {can("customer.create") && (
        <InlineCreateForm
          title={t.dashboard.customers.add}
          busy={busy}
          fields={[
            // What the API requires (Phase 9): a last name and a phone.
            // The old form demanded a first name instead and every
            // "สมชาย + phone" ended in a 422 (review C3).
            { name: "first_name", label: t.dashboard.fields.firstName },
            { name: "last_name", label: t.dashboard.fields.lastName, required: true },
            { name: "phone", label: t.dashboard.fields.phone, type: "tel", required: true },
            { name: "email", label: t.dashboard.fields.email },
          ]}
          onSubmit={createCustomer}
        />
      )}

      {visible.length === 0 ? (
        <Empty
          message={
            customers.length === 0
              ? t.dashboard.customers.empty
              : `${t.dashboard.customers.noMatch}: “${query}”`
          }
        />
      ) : (
        <ul className="list">
          {visible.map((customer) => (
            <li key={customer.id} className="card" data-stage={customer.stage}>
              {/* The whole row opens the detail view — a list you cannot
                  drill into is a report, not a tool. */}
              <Link
                href={`/liff/sales/customers/${customer.id}`}
                style={{ textDecoration: "none", color: "inherit" }}
              >
              <div className="card-title">
                {fullName(customer)}
                <Badge stage={customer.stage} label={stageLabel(customer.stage)} />
              </div>
              <div className="card-meta">
                <span className="code">{customer.customer_id}</span>
                {customer.phone ? ` · ${customer.phone}` : ""}
                {customer.email ? ` · ${customer.email}` : ""}
              </div>
              </Link>
              {customer.stage === "lead" && (can("customer.update") || can("customer.archive")) && (
                <div className="card-actions">
                  {can("customer.update") && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="primary"
                      onClick={() => void promote(customer)}
                      disabled={busyId === customer.id}
                    >
                      {busyId === customer.id ? t.dashboard.saving : t.dashboard.customers.promote}
                    </button>
                  )}
                  {can("customer.archive") && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="danger"
                      onClick={() => void archive(customer)}
                      disabled={busyId === customer.id}
                    >
                      {t.dashboard.customers.archive}
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </SalesShell>
  );
}
