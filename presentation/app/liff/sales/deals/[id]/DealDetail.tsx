"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell, Badge } from "../../_components";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../../_lib";
import { FieldSection, RecordHead, RelatedHeading } from "../../_record";

type Product = {
  id: string;
  product_name?: string | null;
  qty?: number | string | null;
  quoted_unit_price?: string | number | null;
};

type Deal = {
  id: string;
  deal_id: string;
  stage: string;
  contact_id: string;
  notes?: string | null;
  products?: Product[];
};

type Customer = {
  id: string;
  customer_id: string;
  first_name?: string | null;
  last_name?: string | null;
};

// Only the moves the Phase 9 state machine allows. Offering "won" on an
// already-won deal would show a button whose only outcome is an error.
const NEXT_STAGES: Record<string, string[]> = {
  new: ["proposed", "lost"],
  proposed: ["won", "lost"],
  won: [],
  lost: [],
};

function money(value: unknown): string {
  const n = Number(value ?? 0);
  return Number.isFinite(n)
    ? n.toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";
}

export default function DealDetail({
  liffId,
  dealId,
}: {
  liffId: string;
  dealId: string;
}) {
  const { t } = useLanguage();
  const stageLabel = (stage: string) =>
    (t.deal.stage as Record<string, string>)[stage] ?? stage;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [deal, setDeal] = useState<Deal | null>(null);
  const [customer, setCustomer] = useState<Customer | null>(null);
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
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/deals/${dealId}`,
      { headers },
    );
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    const found = (await response.json()) as Deal;
    setDeal(found);

    // Who this deal is for. A deal row carries only contact_id, and a deal
    // without a name attached is most of the way to useless on a screen
    // someone opens to decide what to do next.
    if (found.contact_id) {
      const customersResponse = await fetch(
        `/api/phase2/licenses/${licenseId}/customers`,
        { headers },
      );
      if (customersResponse.ok) {
        const customers = (await customersResponse.json()) as Customer[];
        setCustomer(customers.find((c) => c.id === found.contact_id) ?? null);
      }
    }
    say("");
  }, [dealId, licenseId, say, t, token]);

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

  const canEdit = permissions.has("deal.update");

  async function saveFields(changes: Record<string, string | null>) {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/deals/${dealId}`,
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

  async function setStage(stage: string) {
    if (!deal) return;
    setBusy(true);
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
      setBusy(false);
    }
  }

  async function removeProduct(product: Product) {
    if (!deal) return;
    if (!window.confirm(`${t.common.delete}: ${product.product_name ?? ""}?`)) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/deals/${deal.id}/products/${product.id}`,
        { method: "DELETE", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      say(t.dashboard.saved, "ok");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  const items = deal?.products ?? [];
  const subtotal = items.reduce(
    (sum, p) => sum + Number(p.qty ?? 0) * Number(p.quoted_unit_price ?? 0),
    0,
  );

  return (
    <AppShell
      title={deal?.deal_id ?? t.deal.title}
      back="/liff/sales/deals"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {deal && (
        <>
          <RecordHead
            stage={deal.stage}
            title={deal.deal_id}
            badge={<Badge stage={deal.stage} label={stageLabel(deal.stage)} />}
            subtitle={
              customer ? (
                <Link href={`/liff/sales/customers/${customer.id}`}>
                  {[customer.first_name, customer.last_name].filter(Boolean).join(" ")}{" "}
                  ({customer.customer_id})
                </Link>
              ) : null
            }
            actions={
              canEdit && NEXT_STAGES[deal.stage]?.length
                ? NEXT_STAGES[deal.stage].map((stage) => (
                    <button
                      key={stage}
                      type="button"
                      className="btn"
                      data-variant={stage === "won" ? "primary" : undefined}
                      onClick={() => void setStage(stage)}
                      disabled={busy}
                    >
                      {t.dashboard.deals.changeTo.replace("{stage}", stageLabel(stage))}
                    </button>
                  ))
                : null
            }
          />

          <FieldSection
            title={t.deal.title}
            canEdit={canEdit}
            record={deal as unknown as Record<string, unknown>}
            onSave={saveFields}
            fields={[
              { name: "deal_id", label: t.dashboard.fields.code },
              { name: "notes", label: t.dashboard.fields.notes, editable: true, type: "textarea" },
            ]}
          />

          <RelatedHeading
            title={t.dashboard.deals.lineItems.replace("{count}", "")}
            count={items.length}
          />
          {items.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.deals.noLineItems}</p>
            </div>
          ) : (
            <>
              <ul className="list">
                {items.map((product) => (
                  <li key={product.id} className="card">
                    <div className="card-title">{product.product_name ?? "—"}</div>
                    <div className="card-meta">
                      {`${product.qty ?? 0} × ${money(product.quoted_unit_price)} = `}
                      <strong>
                        {money(
                          Number(product.qty ?? 0) *
                            Number(product.quoted_unit_price ?? 0),
                        )}
                      </strong>
                    </div>
                    {canEdit && (
                      <div className="card-actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          onClick={() => void removeProduct(product)}
                          disabled={busy}
                        >
                          {t.common.delete}
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              <div className="totals">
                <span>{t.dashboard.deals.subtotal}</span>
                <strong>{money(subtotal)}</strong>
              </div>
            </>
          )}
        </>
      )}
    </AppShell>
  );
}
