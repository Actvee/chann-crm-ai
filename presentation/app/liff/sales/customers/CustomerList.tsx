"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell, Badge, CompanyPicker, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { InlineCreateForm } from "../../_inline-create";

import { Membership, fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

type Customer = {
  id: string;
  customer_id: string;
  first_name?: string | null;
  last_name?: string | null;
  stage: string;
  phone?: string | null;
  email?: string | null;
};

function fullName(customer: Customer): string {
  return [customer.first_name, customer.last_name].filter(Boolean).join(" ") || "—";
}

export default function CustomerList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  // Customer stages are only two, and both already have names in the
  // dictionary under their own sections.
  const stageLabel = (stage: string) =>
    stage === "contact" ? t.customer.title : t.customer.lead;
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [busyId, setBusyId] = useState("");
  const [query, setQuery] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

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

  async function createCustomer(values: Record<string, string>) {
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
        const detail = await response.json().catch(() => ({}));
        const body = detail.detail;
        // A duplicate phone names the record that already holds it, so
        // the answer is "here it is" rather than "that failed".
        say(
          body && typeof body === "object" && body.error === "duplicate"
            ? t.dashboard.customers.duplicate.replace(
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
          response.status === 403
            ? t.dashboard.customers.promoteDenied
            : `${t.common.error} (${response.status})`,
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
  const visible = needle
    ? customers.filter((customer) =>
        [fullName(customer), customer.phone, customer.email, customer.customer_id]
          .map((value) => String(value ?? "").toLowerCase())
          .some((value) => value.includes(needle)),
      )
    : customers;

  return (
    <AppShell
      title={t.customer.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      <label className="field">
        <span>{t.dashboard.search}</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t.dashboard.customers.searchHint}
          type="search"
        />
      </label>

      <Count shown={visible.length} total={customers.length} />

      {permissions.has("customer.create") && (
        <InlineCreateForm
          title={t.dashboard.customers.add}
          busy={busy}
          fields={[
            { name: "first_name", label: t.dashboard.fields.firstName, required: true },
            { name: "last_name", label: t.dashboard.fields.lastName },
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
              {customer.stage === "lead" && (
                <div className="card-actions">
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => void promote(customer)}
                    disabled={busyId === customer.id}
                  >
                    {busyId === customer.id ? t.dashboard.saving : t.dashboard.customers.promote}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
