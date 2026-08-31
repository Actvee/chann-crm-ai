"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell, Badge } from "../../_components";
import { Membership, initLiffSession, proxyHeaders } from "../../_lib";

type Customer = {
  id: string;
  customer_id: string;
  first_name?: string | null;
  last_name?: string | null;
  stage: string;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  notes?: string | null;
};

type Deal = {
  id: string;
  deal_id: string;
  stage: string;
  contact_id: string;
  products?: unknown[];
};

function fullName(c: Customer | null): string {
  if (!c) return "";
  return [c.first_name, c.last_name].filter(Boolean).join(" ") || "—";
}

export default function CustomerDetail({
  liffId,
  customerId,
}: {
  liffId: string;
  customerId: string;
}) {
  const { t } = useLanguage();
  const stageLabel = (stage: string) =>
    stage === "contact" ? t.customer.title : t.customer.lead;
  const dealStageLabel = (stage: string) =>
    (t.deal.stage as Record<string, string>)[stage] ?? stage;

  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const headers = proxyHeaders(token, licenseId);
    // Both lists in parallel: the detail view is only useful once it can
    // show the customer AND their deals, so waiting for them in sequence
    // just makes the page slower for no benefit.
    const [customersResponse, dealsResponse] = await Promise.all([
      fetch(`/api/phase2/licenses/${licenseId}/customers`, { headers }),
      fetch(`/api/phase2/licenses/${licenseId}/deals`, { headers }),
    ]);
    if (!customersResponse.ok) {
      throw new Error(
        customersResponse.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${customersResponse.status})`,
      );
    }
    const customers = (await customersResponse.json()) as Customer[];
    const found = customers.find((c) => c.id === customerId) ?? null;
    setCustomer(found);

    if (dealsResponse.ok) {
      const all = (await dealsResponse.json()) as Deal[];
      setDeals(all.filter((d) => d.contact_id === customerId));
    }
    say("");
  }, [customerId, licenseId, say, t, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [licenseId, load, say, t, token]);

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
  }, [liffId, say, t]);

  async function promote() {
    if (!customer) return;
    setBusy(true);
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
      setBusy(false);
    }
  }

  return (
    <AppShell
      title={fullName(customer) || t.customer.title}
      back="/liff/sales/customers"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {memberships.length > 1 && null}

      {customer && (
        <>
          <div className="card" data-stage={customer.stage}>
            <div className="card-title">
              {fullName(customer)}
              <Badge stage={customer.stage} label={stageLabel(customer.stage)} />
            </div>
            <div className="card-meta">
              <span className="code">{customer.customer_id}</span>
            </div>
            <dl style={{ margin: "12px 0 0", display: "grid", gap: 6 }}>
              {([
                [t.dashboard.companyProfile.phone, customer.phone],
                [t.dashboard.companyProfile.email, customer.email],
                [t.dashboard.companyProfile.address, customer.address],
              ] as const)
                .filter(([, value]) => Boolean(value))
                .map(([label, value]) => (
                  <div key={label} style={{ display: "flex", gap: 8, fontSize: 14 }}>
                    <dt style={{ color: "var(--ink-soft)", minWidth: 90 }}>{label}</dt>
                    <dd style={{ margin: 0 }}>{value}</dd>
                  </div>
                ))}
            </dl>
            {customer.stage === "lead" && (
              <div className="card-actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="primary"
                  onClick={() => void promote()}
                  disabled={busy}
                >
                  {busy ? t.dashboard.saving : t.dashboard.customers.promote}
                </button>
              </div>
            )}
          </div>

          {/* The link the flat list never had: a customer is only meaningful
              alongside the work being done for them. */}
          <h2 style={{ fontSize: 15, margin: "22px 0 10px" }}>{t.deal.title}</h2>
          {deals.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.deals.empty}</p>
            </div>
          ) : (
            <ul className="list">
              {deals.map((deal) => (
                <li key={deal.id} className="card" data-stage={deal.stage}>
                  <Link
                    href={`/liff/sales/deals/${deal.id}`}
                    style={{ textDecoration: "none", color: "inherit" }}
                  >
                    <div className="card-title">
                      <span className="code">{deal.deal_id}</span>
                      <Badge stage={deal.stage} label={dealStageLabel(deal.stage)} />
                    </div>
                    <div className="card-meta">
                      {t.dashboard.deals.lineItems.replace(
                        "{count}",
                        String(deal.products?.length ?? 0),
                      )}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </AppShell>
  );
}
