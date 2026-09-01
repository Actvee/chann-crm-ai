"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell, Badge } from "../../_components";
import { Membership, fetchPermissions, initLiffSession, proxyHeaders } from "../../_lib";
import { FieldSection, RecordHead, RelatedHeading } from "../../_record";

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
  const c = t.dashboard.companyProfile;
  const stageLabel = (stage: string) =>
    stage === "contact" ? t.customer.title : t.customer.lead;
  const dealStageLabel = (stage: string) =>
    (t.deal.stage as Record<string, string>)[stage] ?? stage;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const headers = proxyHeaders(token, licenseId);
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
    setCustomer(customers.find((row) => row.id === customerId) ?? null);
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
      const license = session.memberships[0]?.license_id ?? "";
      setToken(session.token);
      setLicenseId(license);
      if (!session.memberships.length) {
        say(t.liff.noCompany, "error");
        return;
      }
      setPermissions(await fetchPermissions(session.token, license));
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say, t]);

  async function createDeal() {
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/deals`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ contact_id: customerId }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        const body = detail.detail;
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
      const created = (await response.json()) as { id: string };
      // Straight into it: someone who opened a deal wants to add what is
      // being sold, and that is the next screen.
      router.push(`/liff/sales/deals/${created.id}`);
    } finally {
      setBusy(false);
    }
  }

  async function saveFields(changes: Record<string, string | null>) {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/customers/${customerId}`,
      {
        method: "PATCH",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify(changes),
      },
    );
    if (!response.ok) {
      say(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.common.error} (${response.status})`,
        "error",
      );
      throw new Error("save failed");
    }
    say(t.dashboard.saved, "ok");
    await load();
  }

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
    } finally {
      setBusy(false);
    }
  }

  const canEdit = permissions.has("customer.update");

  return (
    <AppShell
      title={t.customer.title}
      back="/liff/sales/customers"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {customer && (
        <>
          <RecordHead
            stage={customer.stage}
            title={fullName(customer)}
            subtitle={<span className="code">{customer.customer_id}</span>}
            badge={<Badge stage={customer.stage} label={stageLabel(customer.stage)} />}
            actions={
              customer.stage === "lead" && canEdit ? (
                <button
                  type="button"
                  className="btn"
                  data-variant="primary"
                  onClick={() => void promote()}
                  disabled={busy}
                >
                  {busy ? t.dashboard.saving : t.dashboard.customers.promote}
                </button>
              ) : null
            }
          />

          <FieldSection
            title={t.customer.title}
            canEdit={canEdit}
            record={customer as unknown as Record<string, unknown>}
            onSave={saveFields}
            fields={[
              { name: "first_name", label: t.dashboard.fields.firstName, editable: true },
              { name: "last_name", label: t.dashboard.fields.lastName, editable: true },
              // The customer's own labels, not the company profile's. These
              // read "เบอร์โทรบริษัท" on a person's record because `c` is
              // t.dashboard.companyProfile — borrowed for its field names
              // and wrong for every one of them here.
              { name: "phone", label: t.dashboard.fields.phone, editable: true, type: "tel" },
              { name: "email", label: t.dashboard.fields.email, editable: true, type: "email" },
              { name: "address", label: t.dashboard.fields.address, editable: true, type: "textarea" },
              { name: "notes", label: t.dashboard.fields.notes, editable: true, type: "textarea" },
            ]}
          />

          <RelatedHeading title={t.deal.title} count={deals.length} />

          {/* Opening a deal from the customer you are looking at. The
              customer is already known here, so asking for one — as the
              deal list has to — would be asking a question the page can
              already answer. */}
          {canEdit && !deals.some((d) => d.stage === "new" || d.stage === "proposed") && (
            <div className="actions" style={{ margin: "0 0 12px" }}>
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => void createDeal()}
                disabled={busy}
              >
                {busy ? t.dashboard.saving : t.dashboard.deals.addForThisCustomer}
              </button>
            </div>
          )}

          {deals.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.deals.emptyHere}</p>
            </div>
          ) : (
            <ul className="list">
              {deals.map((deal) => (
                <li key={deal.id} className="card" data-stage={deal.stage}>
                  <Link className="row-link" href={`/liff/sales/deals/${deal.id}`}>
                    <span className="row-body">
                      <span className="card-title">
                        <span className="code">{deal.deal_id}</span>
                        <Badge stage={deal.stage} label={dealStageLabel(deal.stage)} />
                      </span>
                      <span className="card-meta">
                        {t.dashboard.deals.lineItems.replace(
                          "{count}",
                          String(deal.products?.length ?? 0),
                        )}
                      </span>
                    </span>
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
