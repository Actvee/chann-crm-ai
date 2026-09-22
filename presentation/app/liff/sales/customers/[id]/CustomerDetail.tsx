"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Badge } from "../../_components";
import { ConfirmDialog, useConfirm } from "../../../_confirm";
import { useFailureText, useFormatters } from "../../_format";
import { OwnerControl, memberLabel, useSalesMembers, type SalesMember } from "../../_owner";
import { proxyHeaders } from "../../_lib";
import { FieldSection, RecordHead, RelatedHeading, StatusSection } from "../../_record";
import { RelatedActivity } from "../../_related";
import { useSalesSession } from "../../_session";
import { SalesShell } from "../../_shell";
import { useSalesText } from "../../_strings";

type Customer = {
  created_at?: string | null;
  updated_at?: string | null;
  id: string;
  customer_id: string;
  owner_member_id?: string | null;
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

type InvoiceRow = {
  id: string;
  invoice_id: string;
  status: string;
  total: string;
  outstanding: string;
  is_overdue: boolean;
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
  const s = useSalesText();
  const failureText = useFailureText();
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const stageLabel = (stage: string) =>
    stage === "contact" ? t.customer.title : t.customer.lead;
  const dealStageLabel = (stage: string) =>
    (t.deal.stage as Record<string, string>)[stage] ?? stage;

  const { money } = useFormatters();
  const invoiceStatusLabel = (status: string) =>
    (t.invoice.status as Record<string, string>)[status] ?? status;
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [deals, setDeals] = useState<Deal[]>([]);
  // Round 20X: the customer's bills — count, what is owed, and the way
  // to the list. Loaded on its own so a refusal (no invoice.read) leaves
  // the rest of the page whole.
  const [invoices, setInvoices] = useState<InvoiceRow[] | null>(null);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const headers = proxyHeaders(token, licenseId);
    // One customer, not the whole book (review C10): the page used to
    // download every customer in the shop to find this one.
    const [customerResponse, dealsResponse] = await Promise.all([
      fetch(`/api/phase2/licenses/${licenseId}/customers/${customerId}`, { headers }),
      fetch(`/api/phase2/licenses/${licenseId}/deals`, { headers }),
    ]);
    if (!customerResponse.ok) {
      throw new Error(
        customerResponse.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${customerResponse.status})`,
      );
    }
    setCustomer((await customerResponse.json()) as Customer);
    if (!dealsResponse.ok) {
      // "No deals for this customer" and "could not load deals" are
      // different facts; showing the first when the second is true
      // misleads whoever is deciding what to do next.
      throw new Error(`${t.dashboard.loadFailed} (${dealsResponse.status})`);
    }
    const all = (await dealsResponse.json()) as Deal[];
    setDeals(all.filter((d) => d.contact_id === customerId));
    say("");
  }, [customerId, licenseId, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [session.ready, load, say, t]);

  useEffect(() => {
    if (!session.ready || !token || !licenseId || !permissions.has("invoice.read")) return;
    let cancelled = false;
    void (async () => {
      try {
        const search = new URLSearchParams({ contact_id: customerId, limit: "50" });
        const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices?${search}`, {
          headers: proxyHeaders(token, licenseId),
          cache: "no-store",
        });
        if (!cancelled && response.ok) setInvoices((await response.json()) as InvoiceRow[]);
      } catch {
        // The section simply does not appear; the page's own error line
        // is for the customer record.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session.ready, token, licenseId, customerId, permissions]);

  async function createDeal() {
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/deals`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ contact_id: customerId }),
      });
      if (!response.ok) {
        const failure = await failureText(response);
        const detail = await response.json().catch(() => ({}));
        const body = detail.detail;
        say(
          body && typeof body === "object" && body.error === "duplicate"
            ? t.dashboard.deals.alreadyOpen.replace(
                "{code}", String(body.existing_code ?? ""),
              )
            : failure,
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
      say(await failureText(response), "error");
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
          response.status === 403 ? t.dashboard.customers.promoteDenied : await failureText(response),
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

  const can = (key: string) => !session.suspended && permissions.has(key);
  const canEdit = can("customer.update");
  // Round 20V: handing the customer to a colleague. The roster is fetched
  // only for someone who holds the key; the control asks before it writes.
  const canTransfer = can("reassign_records");
  const members = useSalesMembers(token, licenseId, canTransfer && session.ready);

  async function handTo(member: SalesMember) {
    if (!customer) return;
    const name = memberLabel(member, member.chann_uid);
    const ok = await ask({
      action: s.transfer.action.replace("{name}", name),
      target: fullName(customer),
      code: customer.customer_id,
      affects: [s.transfer.affects.replace("{name}", name)],
      reversible: s.transfer.reversible,
      confirmLabel: s.transfer.action.replace("{name}", name),
    });
    if (!ok) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/customers/${customer.id}/owner`, {
        method: "PATCH",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ owner_member_id: member.id }),
      });
      if (!response.ok) {
        say(response.status === 403 ? s.transfer.denied : await failureText(response), "error");
        return;
      }
      say(s.transfer.done.replace("{label}", `${fullName(customer)} (${customer.customer_id})`).replace("{name}", name), "ok");
      await load();
    } catch {
      say(t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SalesShell
      session={session}
      title={t.customer.title}
      back="/liff/sales/customers"
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {customer && (
        <>
          <RecordHead
            createdAt={customer.created_at}
            updatedAt={customer.updated_at}
            stage={customer.stage}
            title={fullName(customer)}
            subtitle={<span className="code">{customer.customer_id}</span>}
            badge={<Badge stage={customer.stage} label={stageLabel(customer.stage)} />}
          />

          {/* Lead → customer is a change of what this record IS, so it
              lives in the status block with its own heading rather than
              beside the buttons that do things (owner, 22 ก.ย. 2569). */}
          <StatusSection
            title={t.dashboard.record.statusOf.replace("{record}", t.customer.title)}
            current={<Badge stage={customer.stage} label={stageLabel(customer.stage)} />}
            moves={
              customer.stage === "lead" && canEdit ? (
                <button type="button" className="btn" onClick={() => void promote()} disabled={busy}>
                  {busy ? t.dashboard.saving : t.dashboard.customers.promote}
                </button>
              ) : undefined
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

          {canTransfer && (
            <OwnerControl members={members} ownerId={customer.owner_member_id} busy={busy} onPick={(m) => void handTo(m)} />
          )}

          <RelatedHeading title={t.deal.title} count={deals.length} />

          {/* Opening a deal from the customer you are looking at. The
              customer is already known here, so asking for one — as the
              deal list has to — would be asking a question the page can
              already answer. Gated on deal.create, which is what the
              route checks (review C7): customer.update was the wrong key. */}
          {can("deal.create") && !deals.some((d) => d.stage === "new" || d.stage === "proposed") && (
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

          {invoices !== null && (
            <>
              <RelatedHeading title={t.dashboard.invoices.related} count={invoices.length} />
              {/* Round 20X: "ออกใบแจ้งหนี้" from the customer — the form
                  opens with this customer chosen and the deal still to be
                  picked, because a bill always belongs to a deal (owner,
                  22 ก.ย. 2569). Offered whenever the person may create
                  one: with no deal to bill, the form says "สร้างดีลก่อน"
                  and links back here, which is clearer than a button
                  that is missing for a reason nobody can see. */}
              {can("invoice.create") && (
                <div className="actions" style={{ margin: "0 0 12px" }}>
                  <Link className="btn" data-variant="primary" href={`/liff/sales/invoices?contact_id=${customerId}&create=1`}>
                    {t.dashboard.invoices.forThisCustomer}
                  </Link>
                </div>
              )}
              {invoices.length === 0 ? (
                <div className="empty">
                  <p>{t.dashboard.invoices.relatedNone}</p>
                </div>
              ) : (
                <>
                  <p className="count">
                    {t.dashboard.invoices.relatedOutstanding.replace(
                      "{total}",
                      money(invoices.reduce((sum, row) => sum + Number(row.outstanding ?? 0), 0)),
                    )}
                  </p>
                  <ul className="list">
                    {invoices.slice(0, 5).map((row) => (
                      <li key={row.id} className="card" data-stage={row.status} data-overdue={row.is_overdue ? "true" : undefined}>
                        <Link className="row-link" href={`/liff/sales/invoices?contact_id=${customerId}`}>
                          <span className="row-body">
                            <span className="card-title">
                              <span className="code">{row.invoice_id}</span>
                              <Badge stage={row.status} label={invoiceStatusLabel(row.status)} />
                              {row.is_overdue && <Badge stage="overdue" label={t.dashboard.invoices.overdue} />}
                            </span>
                            <span className="card-meta">
                              {t.dashboard.invoices.total} <span className="money-line">{money(row.total)}</span>
                              {Number(row.outstanding) > 0 && (
                                <>
                                  {" · "}
                                  {t.dashboard.invoices.outstanding} <span className="money-line">{money(row.outstanding)}</span>
                                </>
                              )}
                            </span>
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                  <div className="actions" style={{ margin: "0 0 12px" }}>
                    <Link className="btn" href={`/liff/sales/invoices?contact_id=${customerId}`}>
                      {t.dashboard.invoices.relatedAll}
                    </Link>
                  </div>
                </>
              )}
            </>
          )}

          <RelatedActivity
            licenseId={licenseId}
            token={token}
            entityType="customer"
            entityId={customerId}
            permissions={permissions}
            readOnly={session.suspended}
          />
        </>
      )}
      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={busy} />
    </SalesShell>
  );
}
