"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Count, Empty } from "../_components";
import { RelatedLinks } from "../_record";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { ListFilters, optionsFrom } from "../../_filters";
import { usePagedList } from "../../_paged-list";
import { ListControls, byNewest, byOldest, useListControls } from "../../_list-controls";
import { Sheet } from "../../_sheet";
import { FieldRow } from "../../_field-row";
import { ConfirmDialog, useConfirm } from "../../_confirm";

import { useFailureText, useFormatters } from "../_format";
import { openExternal, proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { InvoiceCreateSheet, type CreatedInvoice } from "./_create-sheet";

/**
 * Round 20V — invoices, payments and receipts (owner, 21 ก.ย. 2569:
 * "ทำข้อ 2 … รวมเอาเรื่อง invoice").
 *
 * One page: the list, and a sheet for the invoice in hand. The sheet is
 * where the money moves — issue the PDF, record a payment (a deposit,
 * an instalment, the balance), issue the receipt, void — so those
 * buttons sit next to the lines and the ledger they change, never on a
 * row nobody has read yet (the quote list's lesson, 17 ก.ย. 2569).
 *
 * Money is printed in the data font and right-aligned so two rows
 * compare by eye; a status is always a word on a badge, the colour only
 * echoes it (ui-ux-pro-max: never colour alone). The amount field uses
 * inputMode="decimal" so a phone opens the number keyboard, and every
 * field has a visible label.
 *
 * Round 20X (owner, 22 ก.ย. 2569: "invoice ต้องมีตัวเลือกให้ผูกกับ deal หรือ
 * ลูกค้าได้"): the page can now MAKE an invoice — "สร้างใบแจ้งหนี้" opens
 * the form in `_create-sheet.tsx` — and honours `?contact_id=` and
 * `?deal_id=` as filters, so the customer page and the deal page can
 * link to "their" bills. `&create=1` on either opens the form prefilled.
 */

type Payment = {
  id: string;
  amount: string;
  method: string;
  paid_at: string;
  reference?: string | null;
  note?: string | null;
};

type Line = {
  line_no: number;
  product_name: string;
  qty: number;
  unit_price: string;
  line_total: string;
  notes?: string | null;
};

type Invoice = {
  created_at?: string | null;
  id: string;
  invoice_id: string;
  status: string;
  contact_id?: string | null;
  quote_id?: string | null;
  deal_id?: string | null;
  issue_date?: string | null;
  due_date?: string | null;
  total: string;
  paid_amount: string;
  outstanding: string;
  is_overdue: boolean;
  note?: string | null;
  generated_document_id?: string | null;
  receipt_document_id?: string | null;
  data_snapshot?: {
    customer?: { name?: string };
    quote?: { quote_id?: string };
    deal?: { deal_id?: string };
    line_items?: Line[];
    totals?: { subtotal?: string; discount_amount?: string | null; vat_amount?: string | null; grand_total?: string };
  } | null;
  payments?: Payment[];
};

const METHODS = ["transfer", "cash", "promptpay", "card", "other"] as const;

export default function InvoiceList({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const failureText = useFailureText();
  const { money, shortDate } = useFormatters();
  const statusLabel = (status: string) =>
    (t.invoice.status as Record<string, string>)[status] ?? status;
  const methodLabel = (method: string) =>
    (t.dashboard.invoices.methods as Record<string, string>)[method] ?? method;
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [open, setOpen] = useState<Invoice | null>(null);
  const [paying, setPaying] = useState(false);
  const [form, setForm] = useState({ amount: "", method: "transfer", paid_at: "", reference: "" });
  // Round 20X: the create form and the URL's filters.
  const [creating, setCreating] = useState(false);
  const [contactFilter, setContactFilter] = useState("");
  const [dealFilter, setDealFilter] = useState("");
  const [filterName, setFilterName] = useState("");
  const [prefill, setPrefill] = useState<{ contactId?: string; dealId?: string }>({});
  const [wantedId, setWantedId] = useState("");

  useEffect(() => {
    // Read once, on the client: the page is rendered dynamically and the
    // LIFF redirect can only carry query strings.
    const params = new URLSearchParams(window.location.search);
    const contactId = params.get("contact_id") ?? "";
    const dealId = params.get("deal_id") ?? "";
    // Round 21A: a quote (or a freshly created bill) points straight at
    // one invoice — the page opens it instead of making someone find it.
    const wanted = params.get("invoice_id") ?? "";
    if (wanted) setWantedId(wanted);
    setContactFilter(contactId);
    setDealFilter(dealId);
    setPrefill({ contactId: contactId || undefined, dealId: dealId || undefined });
    if (params.get("create") === "1") setCreating(true);
  }, []);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  const listError = useCallback(
    (_message: string, httpStatus?: number) =>
      say(
        httpStatus === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed}${httpStatus ? ` (${httpStatus})` : ""}`,
        "error",
      ),
    [say, t],
  );
  const list = usePagedList<Invoice>({
    token, licenseId, ready: session.ready,
    path: `licenses/${licenseId}/invoices`,
    params: {
      status_filter: statusFilter, overdue: overdueOnly ? "true" : "",
      contact_id: contactFilter, deal_id: dealFilter,
    },
    onError: listError,
  });
  const invoices = list.rows;
  const load = list.reload;

  // The filter chip names the record, not its id: "เฉพาะลูกค้า สมชาย ใจดี"
  // rather than a UUID nobody can read.
  useEffect(() => {
    if (!session.ready || !token || !licenseId) return;
    let cancelled = false;
    void (async () => {
      try {
        const headers = proxyHeaders(token, licenseId);
        if (dealFilter) {
          const response = await fetch(`/api/phase2/licenses/${licenseId}/deals/${dealFilter}`, { headers });
          if (!cancelled && response.ok) setFilterName(String(((await response.json()) as { deal_id?: string }).deal_id ?? ""));
        } else if (contactFilter) {
          const response = await fetch(`/api/phase2/licenses/${licenseId}/customers/${contactFilter}`, { headers });
          if (!cancelled && response.ok) {
            const row = (await response.json()) as { first_name?: string | null; last_name?: string | null; customer_id?: string };
            setFilterName([row.first_name, row.last_name].filter(Boolean).join(" ") || String(row.customer_id ?? ""));
          }
        } else {
          setFilterName("");
        }
      } catch {
        // The chip falls back to its plain label.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session.ready, token, licenseId, contactFilter, dealFilter]);

  function clearFilters() {
    setContactFilter("");
    setDealFilter("");
    setFilterName("");
    window.history.replaceState(null, "", window.location.pathname);
  }

  /** After the form: the list refreshes and the new bill's own sheet
   *  opens, where the PDF and the payment buttons are. */
  async function created(invoice: CreatedInvoice) {
    setCreating(false);
    await load();
    await openInvoice(invoice as unknown as Invoice);
  }

  useEffect(() => {
    if (session.ready && !list.busy) say("");
  }, [session.ready, list.busy, say]);

  // The invoice a link named (?invoice_id=…): fetched on its own, so it
  // opens even when it is not on the first page of the list.
  useEffect(() => {
    if (!session.ready || !token || !licenseId || !wantedId) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${wantedId}`, {
          headers: proxyHeaders(token, licenseId),
        });
        if (!cancelled && response.ok) {
          setOpen((await response.json()) as Invoice);
          setPaying(false);
        }
      } catch {
        /* the list is still there to find it in */
      } finally {
        if (!cancelled) setWantedId("");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session.ready, token, licenseId, wantedId]);

  const can = (key: string) => !session.suspended && permissions.has(key);
  const canUpdate = can("invoice.update");
  const canVoid = can("invoice.void");
  const canCreate = can("invoice.create");

  /** The row with its ledger: the list leaves `payments` empty on purpose. */
  async function openInvoice(row: Invoice) {
    setOpen(row);
    setPaying(false);
    setForm({ amount: "", method: "transfer", paid_at: "", reference: "" });
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${row.id}`, {
        headers: proxyHeaders(token, licenseId),
      });
      if (response.ok) setOpen((await response.json()) as Invoice);
    } catch {
      // The row already on screen is a complete answer; the ledger is extra.
    }
  }

  async function refreshOpen(id: string) {
    await load();
    const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${id}`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (response.ok) setOpen((await response.json()) as Invoice);
  }

  async function openDocument(documentId: string) {
    say(t.dashboard.working);
    try {
      // A signed https link, never a blob: URL — LINE's browser refuses those.
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/documents/${documentId}/link`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const { url } = (await response.json()) as { url: string };
      openExternal(url);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    }
  }

  async function issue(row: Invoice) {
    const already = Boolean(row.generated_document_id);
    const ok = await ask({
      action: already ? t.dashboard.invoices.reissue : t.dashboard.invoices.issue,
      target: t.dashboard.invoices.invoiceWord,
      code: row.invoice_id,
      affects: [already ? t.dashboard.invoices.reissueAffects : t.dashboard.invoices.issueAffects],
      reversible: t.dashboard.invoices.issueKeeps,
      confirmLabel: already ? t.dashboard.invoices.reissue : t.dashboard.invoices.issue,
    });
    if (!ok) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/issue?allow_reissue=${already}`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const issued = (await response.json()) as { generated_document_id?: string };
      say(`${row.invoice_id} — ${t.dashboard.invoices.issued}`, "ok");
      await refreshOpen(row.id);
      if (issued.generated_document_id) await openDocument(String(issued.generated_document_id));
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  async function savePayment(row: Invoice) {
    setBusy(true);
    say(t.dashboard.working);
    try {
      const amount = form.amount.trim().replace(/,/g, "");
      const body: Record<string, unknown> = {
        method: form.method,
        reference: form.reference.trim() || null,
        // An empty amount means "the whole balance" — said on the field.
        full: amount === "",
      };
      if (amount !== "") body.amount = amount;
      if (form.paid_at) body.paid_at = `${form.paid_at}T12:00:00+07:00`;
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/payments`,
        { method: "POST", headers: proxyHeaders(token, licenseId), body: JSON.stringify(body) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      say(`${row.invoice_id} — ${t.dashboard.invoices.paymentSaved}`, "ok");
      setPaying(false);
      setForm({ amount: "", method: "transfer", paid_at: "", reference: "" });
      await refreshOpen(row.id);
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  async function issueReceipt(row: Invoice) {
    const already = Boolean(row.receipt_document_id);
    const ok = await ask({
      action: already ? t.dashboard.invoices.reissueReceipt : t.dashboard.invoices.issueReceipt,
      target: t.dashboard.invoices.invoiceWord,
      code: row.invoice_id,
      affects: [t.dashboard.invoices.receiptAffects],
      reversible: t.dashboard.invoices.receiptKeeps,
      confirmLabel: already ? t.dashboard.invoices.reissueReceipt : t.dashboard.invoices.issueReceipt,
    });
    if (!ok) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/receipt?allow_reissue=${already}`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const issued = (await response.json()) as { receipt_document_id?: string };
      say(`${row.invoice_id} — ${t.dashboard.invoices.receiptIssued}`, "ok");
      await refreshOpen(row.id);
      if (issued.receipt_document_id) await openDocument(String(issued.receipt_document_id));
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  async function voidInvoice(row: Invoice) {
    const ok = await ask({
      action: t.dashboard.invoices.voidAction,
      target: t.dashboard.invoices.invoiceWord,
      code: row.invoice_id,
      affects: [t.dashboard.invoices.voidAffects],
      permanent: true,
      confirmLabel: t.dashboard.invoices.voidAction,
    });
    if (!ok) return;
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${row.id}/void`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      say(`${row.invoice_id} — ${t.dashboard.invoices.voided}`, "ok");
      await refreshOpen(row.id);
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  const sorts = [
    { key: "newest", label: t.dashboard.list.newest, compare: byNewest<Invoice> },
    { key: "oldest", label: t.dashboard.list.oldest, compare: byOldest<Invoice> },
    {
      key: "code",
      label: t.dashboard.list.byCode,
      compare: (a: Invoice, b: Invoice) => b.invoice_id.localeCompare(a.invoice_id),
    },
  ];
  // Status, search and overdue are applied by the database (round 20N).
  const controls = useListControls(invoices, sorts, "newest");
  const visible = controls.visible;
  const customerName = (row: Invoice) => row.data_snapshot?.customer?.name || "—";
  const isOpen = (row: Invoice) => row.status === "issued" || row.status === "partially_paid";
  const lines = open?.data_snapshot?.line_items ?? [];
  const totals = open?.data_snapshot?.totals;

  return (
    <SalesShell
      session={session}
      title={t.dashboard.invoices.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <ListFilters
        query={list.query}
        onQuery={list.setQuery}
        status={statusFilter}
        statuses={optionsFrom(t.invoice.status as Record<string, string>)}
        onStatus={setStatusFilter}
      >
        <ListControls
          sorts={sorts}
          sortKey={controls.sortKey}
          onSort={controls.setSortKey}
          from={controls.from}
          to={controls.to}
          onFrom={controls.setFrom}
          onTo={controls.setTo}
        />
        <label className="filter-check">
          <input
            type="checkbox"
            checked={overdueOnly}
            onChange={(event) => setOverdueOnly(event.target.checked)}
          />{" "}
          {t.dashboard.invoices.overdueOnly}
        </label>
      </ListFilters>

      <div className="list-head">
        {/* A list narrowed by a URL the person cannot see is a puzzle
            (the filter bar's own rule): the narrowing is named, by the
            record's name or code, with the way out beside it. */}
        {contactFilter || dealFilter ? (
          <p className="count">
            {dealFilter
              ? t.dashboard.invoices.filteredByDeal.replace("{code}", filterName || dealFilter)
              : t.dashboard.invoices.filteredByCustomer.replace("{name}", filterName || contactFilter)}
          </p>
        ) : (
          <Count shown={visible.length} total={list.total ?? invoices.length} />
        )}
        {(canCreate || contactFilter || dealFilter) && (
          <div className="list-tools">
            {(contactFilter || dealFilter) && (
              <button type="button" className="btn" data-variant="quiet" onClick={clearFilters}>
                {t.dashboard.invoices.clearFilter}
              </button>
            )}
            {canCreate && (
              <button type="button" className="btn" data-variant="primary" onClick={() => setCreating(true)}>
                {t.dashboard.invoices.create}
              </button>
            )}
          </div>
        )}
      </div>

      {visible.length === 0 ? (
        <Empty
          message={
            list.searching
              ? t.dashboard.opening
              : list.query || statusFilter || overdueOnly
                ? t.dashboard.noMatch
                : t.dashboard.invoices.empty
          }
        />
      ) : (
        <ul className="list">
          {visible.map((row) => (
            <li
              key={row.id}
              className="card"
              data-stage={row.status}
              data-overdue={row.is_overdue ? "true" : undefined}
            >
              <button type="button" className="row-link" onClick={() => void openInvoice(row)}>
                <span className="row-body">
                  <span className="card-title">
                    <span className="code">{row.invoice_id}</span>
                    <Badge stage={row.status} label={statusLabel(row.status)} />
                    {row.is_overdue && (
                      <Badge stage="overdue" label={t.dashboard.invoices.overdue} />
                    )}
                  </span>
                  <span className="card-meta">
                    {customerName(row)}
                    {" · "}
                    {t.dashboard.invoices.total} <span className="money-line">{money(row.total)}</span>
                    {isOpen(row) && (
                      <>
                        {" · "}
                        {t.dashboard.invoices.outstanding}{" "}
                        <span className="money-line">{money(row.outstanding)}</span>
                      </>
                    )}
                    {row.due_date && isOpen(row) && (
                      <>
                        {" · "}
                        {t.dashboard.invoices.due} {shortDate(row.due_date)}
                      </>
                    )}
                    {!row.generated_document_id && row.status === "draft" && (
                      <>
                        {" · "}
                        {t.dashboard.invoices.notIssued}
                      </>
                    )}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {list.hasMore && (
        <div className="actions">
          <button type="button" className="btn" disabled={list.busy} onClick={list.loadMore}>
            {list.busy ? t.dashboard.opening : t.dashboard.list.loadMore}
          </button>
        </div>
      )}

      {!canUpdate && !session.suspended && (
        <p className="footnote">{t.dashboard.invoices.needsUpdate}</p>
      )}
      <p className="footnote">{t.dashboard.invoices.note}</p>

      <Sheet open={Boolean(open)} title={open?.invoice_id ?? ""} onClose={() => setOpen(null)}>
        {open && (
          <>
            <p className="card-meta">
              <Badge stage={open.status} label={statusLabel(open.status)} />
              {open.is_overdue && <Badge stage="overdue" label={t.dashboard.invoices.overdue} />}
              {" "}
              {customerName(open)}
            </p>

            {/* Owner, 22 ก.ย. 2569: "จากใบแจ้งหนี้ก็ควรกดไปที่ record ที่
                เกี่ยวข้องได้ด้วย" — the codes were printed as text, so the
                way back to the deal was the search box. */}
            <RelatedLinks
              items={[
                ...(open.contact_id
                  ? [{ href: `/liff/sales/customers/${open.contact_id}`, label: t.customer.title, code: customerName(open) }]
                  : []),
                ...(open.deal_id
                  ? [{ href: `/liff/sales/deals/${open.deal_id}`, label: t.deal.title, code: open.data_snapshot?.deal?.deal_id ?? "" }]
                  : []),
                ...(open.quote_id
                  ? [{ href: `/liff/sales/quotes/${open.quote_id}`, label: t.quote.title, code: open.data_snapshot?.quote?.quote_id ?? "" }]
                  : []),
              ]}
            />

            <dl className="fields">
              <FieldRow label={t.dashboard.invoices.total}>
                <span className="money-line">{money(open.total)}</span>
              </FieldRow>
              <FieldRow label={t.dashboard.invoices.paid}>
                <span className="money-line">{money(open.paid_amount)}</span>
              </FieldRow>
              <FieldRow label={t.dashboard.invoices.outstanding}>
                <span className="money-line">{money(open.outstanding)}</span>
              </FieldRow>
              <FieldRow label={t.dashboard.invoices.due} empty={!open.due_date}>
                {open.due_date ? shortDate(open.due_date) : "—"}
              </FieldRow>
            </dl>

            <div className="actions">
              {open.generated_document_id && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void openDocument(String(open.generated_document_id))}
                >
                  {t.dashboard.invoices.openPdf}
                </button>
              )}
              {open.receipt_document_id && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void openDocument(String(open.receipt_document_id))}
                >
                  {t.dashboard.invoices.openReceipt}
                </button>
              )}
              {canUpdate && open.status !== "void" && open.status !== "paid" && (
                <button
                  type="button"
                  className="btn"
                  data-variant={open.generated_document_id ? undefined : "primary"}
                  disabled={busy}
                  onClick={() => void issue(open)}
                >
                  {open.generated_document_id ? t.dashboard.invoices.reissue : t.dashboard.invoices.issue}
                </button>
              )}
              {canUpdate && isOpen(open) && !paying && (
                <button
                  type="button"
                  className="btn"
                  data-variant="primary"
                  disabled={busy}
                  onClick={() => setPaying(true)}
                >
                  {t.dashboard.invoices.recordPayment}
                </button>
              )}
              {canUpdate && open.status === "paid" && (
                <button type="button" className="btn" data-variant="primary" disabled={busy} onClick={() => void issueReceipt(open)}>
                  {open.receipt_document_id ? t.dashboard.invoices.reissueReceipt : t.dashboard.invoices.issueReceipt}
                </button>
              )}
            </div>

            {/* Undoing a bill is not one of the things you do WITH it: its
                own row, under a rule, in the danger colour — and the word
                is "ยกเลิก", because a bill that went out is voided, never
                deleted (owner, 22 ก.ย. 2569). */}
            {canVoid && (open.status === "draft" || open.status === "issued") && Number(open.paid_amount) === 0 && (
              <div className="actions record-danger">
                <button type="button" className="btn" data-variant="danger" disabled={busy} onClick={() => void voidInvoice(open)}>
                  {t.dashboard.invoices.voidAction}
                </button>
              </div>
            )}

            {paying && (
              <form
                className="section"
                onSubmit={(event) => {
                  event.preventDefault();
                  void savePayment(open);
                }}
              >
                <div className="section-head">
                  <h2>{t.dashboard.invoices.recordPayment}</h2>
                </div>
                <dl className="fields">
                  <FieldRow label={t.dashboard.invoices.amount}>
                    {(id) => (
                      <input
                        id={id}
                        inputMode="decimal"
                        placeholder={money(open.outstanding)}
                        value={form.amount}
                        onChange={(event) => setForm({ ...form, amount: event.target.value })}
                      />
                    )}
                  </FieldRow>
                  <p className="hint">{t.dashboard.invoices.amountHint}</p>
                  <FieldRow label={t.dashboard.invoices.method}>
                    {(id) => (
                      <select
                        id={id}
                        value={form.method}
                        onChange={(event) => setForm({ ...form, method: event.target.value })}
                      >
                        {METHODS.map((m) => (
                          <option key={m} value={m}>{methodLabel(m)}</option>
                        ))}
                      </select>
                    )}
                  </FieldRow>
                  <FieldRow label={t.dashboard.invoices.paidAt}>
                    {(id) => (
                      <input
                        id={id}
                        type="date"
                        value={form.paid_at}
                        onChange={(event) => setForm({ ...form, paid_at: event.target.value })}
                      />
                    )}
                  </FieldRow>
                  <FieldRow label={t.dashboard.invoices.reference}>
                    {(id) => (
                      <input
                        id={id}
                        value={form.reference}
                        onChange={(event) => setForm({ ...form, reference: event.target.value })}
                      />
                    )}
                  </FieldRow>
                </dl>
                <div className="actions">
                  <button type="submit" className="btn" data-variant="primary" disabled={busy}>
                    {busy ? t.dashboard.working : t.dashboard.invoices.save}
                  </button>
                  <button type="button" className="btn" data-variant="quiet" disabled={busy} onClick={() => setPaying(false)}>
                    {t.dashboard.invoices.cancel}
                  </button>
                </div>
              </form>
            )}

            <section className="section">
              <div className="section-head">
                <h2>{t.dashboard.invoices.lines} ({lines.length})</h2>
              </div>
              <ul className="list">
                {lines.map((line) => (
                  <li key={line.line_no} className="card">
                    <div className="card-title">{line.product_name}</div>
                    <div className="card-meta">
                      {line.qty} × <span className="money-line">{money(line.unit_price)}</span>
                      {" = "}
                      <span className="money-line">{money(line.line_total)}</span>
                      {line.notes ? ` · ${line.notes}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
              {totals && (
                <div className="totals">
                  <span>{t.dashboard.invoices.total}</span>
                  <strong>{money(totals.grand_total)}</strong>
                </div>
              )}
            </section>

            <section className="section">
              <div className="section-head">
                <h2>{t.dashboard.invoices.payments} ({(open.payments ?? []).length})</h2>
              </div>
              {(open.payments ?? []).length === 0 ? (
                <div className="empty">
                  <p>{t.dashboard.invoices.noPayments}</p>
                </div>
              ) : (
                <ul className="list">
                  {(open.payments ?? []).map((payment) => (
                    <li key={payment.id} className="card">
                      <div className="card-title">
                        <span className="money-line">{money(payment.amount)}</span>{" "}
                        <Badge stage="paid" label={methodLabel(payment.method)} />
                      </div>
                      <div className="card-meta">
                        {shortDate(payment.paid_at)}
                        {payment.reference ? ` · ${payment.reference}` : ""}
                        {payment.note ? ` · ${payment.note}` : ""}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <span hidden data-locale={locale} />
          </>
        )}
      </Sheet>

      <InvoiceCreateSheet
        open={creating}
        onClose={() => setCreating(false)}
        token={token}
        licenseId={licenseId}
        prefill={prefill}
        onCreated={created}
        say={say}
      />

      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={busy} />
    </SalesShell>
  );
}
