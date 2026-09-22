"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { ConfirmDialog, useConfirm } from "../../_confirm";
import { FieldRow } from "../../_field-row";
import { Sheet } from "../../_sheet";
import { useFailureText, useFormatters } from "../_format";
import { proxyHeaders } from "../_lib";

/**
 * Round 20X — making an invoice from the invoices page.
 *
 * Owner, 22 ก.ย. 2569: "invoice ต้องมีตัวเลือกให้ผูกกับ deal หรือลูกค้าได้" and,
 * the same day, "ใบแจ้งหนี้จะไม่ผูกกับลูกค้าโดยตรง อย่างน้อยจะมีดีลเกิดขึ้น". So
 * the form starts where a person starts — with the customer — and always
 * lands on one of that customer's deals, taking the deal's quotation when
 * it has one that was sent or accepted. There is no line editor: the lines
 * are the deal's (or the quote's), shown read-only with their total, the
 * same rows the PDF will print. A customer with no deal is told so, with
 * the way to the page that opens one, rather than shown an empty select.
 *
 * Three fetches, each on its own so a failed one does not take the others
 * down: the customer search (server-side, the same `q` the customer list
 * uses — a shop with 800 customers cannot scroll a select), the
 * customer's deals, and the deal's quotes. `?deal_id=` in the URL fills
 * customer and deal both; `?contact_id=` fills the customer.
 *
 * ui-ux-pro-max: every control has a visible label (FieldRow), targets
 * are 44px (.btn, .picker-options), inputs are 16px so iOS does not zoom,
 * and "สร้างและออกเอกสาร" — which makes a numbered tax document — asks
 * first through the same dialog every other issue button uses.
 */

type CustomerRow = {
  id: string;
  customer_id: string;
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
};

type Product = {
  id: string;
  product_name?: string | null;
  qty?: number | string | null;
  quoted_unit_price?: string | number | null;
};

type DealRow = {
  id: string;
  deal_id: string;
  stage: string;
  contact_id: string;
  products?: Product[];
};

type QuoteRow = {
  id: string;
  quote_id: string;
  status: string;
  deal_id: string;
  discount_percent?: string | null;
  discount_amount?: string | null;
};

export type CreatedInvoice = {
  id: string;
  invoice_id: string;
  status: string;
  generated_document_id?: string | null;
};

const BILLABLE_QUOTE_STATUSES = ["sent", "accepted"];
const SEARCH_DEBOUNCE_MS = 300;

function customerName(row: CustomerRow | null): string {
  if (!row) return "";
  return [row.first_name, row.last_name].filter(Boolean).join(" ") || row.phone || row.customer_id;
}

export function InvoiceCreateSheet({
  open,
  onClose,
  token,
  licenseId,
  prefill,
  onCreated,
  say,
}: {
  open: boolean;
  onClose: () => void;
  token: string;
  licenseId: string;
  /** From the URL: the deal page's button sends deal_id, the customer page's contact_id. */
  prefill: { contactId?: string; dealId?: string };
  onCreated: (invoice: CreatedInvoice) => Promise<void>;
  say: (message: string, kind?: "ok" | "error") => void;
}) {
  const { t } = useLanguage();
  const copy = t.dashboard.invoices;
  const { money } = useFormatters();
  const failureText = useFailureText();
  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const dealStageLabel = (stage: string) => (t.deal.stage as Record<string, string>)[stage] ?? stage;
  const quoteStatusLabel = (status: string) => (t.quote.status as Record<string, string>)[status] ?? status;

  const [customer, setCustomer] = useState<CustomerRow | null>(null);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<CustomerRow[]>([]);
  const [searching, setSearching] = useState(false);
  const [deals, setDeals] = useState<DealRow[] | null>(null);
  const [dealId, setDealId] = useState("");
  const [quotes, setQuotes] = useState<QuoteRow[]>([]);
  const [quoteId, setQuoteId] = useState("");
  const [quoteLines, setQuoteLines] = useState<Product[] | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const headers = useMemo(() => proxyHeaders(token, licenseId), [token, licenseId]);
  const ready = open && Boolean(token) && Boolean(licenseId);

  // Start clean every time it opens: the last customer's deals must not
  // sit under a new search.
  useEffect(() => {
    if (!open) return;
    setCustomer(null);
    setQuery("");
    setMatches([]);
    setDeals(null);
    setDealId("");
    setQuotes([]);
    setQuoteId("");
    setQuoteLines(null);
    setNote("");
    setProblem("");
  }, [open]);

  // The prefill: a deal names its customer, so ?deal_id= fills both.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    void (async () => {
      try {
        let contactId = prefill.contactId ?? "";
        if (prefill.dealId) {
          const response = await fetch(`/api/phase2/licenses/${licenseId}/deals/${prefill.dealId}`, { headers });
          if (response.ok) {
            const deal = (await response.json()) as DealRow;
            contactId = deal.contact_id;
            if (!cancelled) setDealId(deal.id);
          }
        }
        if (!contactId) return;
        const response = await fetch(`/api/phase2/licenses/${licenseId}/customers/${contactId}`, { headers });
        if (!response.ok || cancelled) return;
        setCustomer((await response.json()) as CustomerRow);
      } catch {
        // The form still works by hand; the prefill was a convenience.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, prefill.contactId, prefill.dealId, licenseId, headers]);

  // The customer search — by the SERVER, debounced, newest request wins.
  useEffect(() => {
    if (!ready || customer) return;
    const needle = query.trim();
    if (!needle) {
      setMatches([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setSearching(true);
      try {
        const search = new URLSearchParams({ q: needle, limit: "20" });
        const response = await fetch(`/api/phase2/licenses/${licenseId}/customers?${search}`, { headers, cache: "no-store" });
        if (cancelled || !response.ok) return;
        setMatches((await response.json()) as CustomerRow[]);
      } catch {
        if (!cancelled) setMatches([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [ready, customer, query, licenseId, headers]);

  // The customer's deals, once there is a customer.
  useEffect(() => {
    if (!ready || !customer) {
      setDeals(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const search = new URLSearchParams({ contact_id: customer.id });
        const response = await fetch(`/api/phase2/licenses/${licenseId}/deals?${search}`, { headers, cache: "no-store" });
        if (cancelled) return;
        if (!response.ok) {
          setProblem(`${t.dashboard.loadFailed} (${response.status})`);
          setDeals([]);
          return;
        }
        setDeals((await response.json()) as DealRow[]);
      } catch {
        if (!cancelled) setDeals([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, customer, licenseId, headers, t]);

  // The deal's quotations, once there is a deal.
  useEffect(() => {
    if (!ready || !dealId) {
      setQuotes([]);
      setQuoteId("");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const search = new URLSearchParams({ deal_id: dealId, limit: "50" });
        const response = await fetch(`/api/phase2/licenses/${licenseId}/quotes?${search}`, { headers, cache: "no-store" });
        if (cancelled || !response.ok) return;
        const rows = ((await response.json()) as QuoteRow[]).filter((q) => BILLABLE_QUOTE_STATUSES.includes(q.status));
        setQuotes(rows);
        // The newest billable quote is the one being billed nine times in
        // ten; offered, and changeable, not forced.
        setQuoteId(rows[0]?.id ?? "");
      } catch {
        if (!cancelled) setQuotes([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, dealId, licenseId, headers]);

  // The quote's own lines (a discount agreed on the offer stays on the
  // bill); the deal's when no quote is chosen.
  useEffect(() => {
    if (!ready || !quoteId) {
      setQuoteLines(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/quotes/${quoteId}/products`, { headers, cache: "no-store" });
        if (cancelled || !response.ok) return;
        const rows = (await response.json()) as Product[];
        setQuoteLines(rows.length ? rows : null);
      } catch {
        if (!cancelled) setQuoteLines(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, quoteId, licenseId, headers]);

  const billable = (deals ?? []).filter((d) => (d.products?.length ?? 0) > 0 && d.stage !== "lost");
  const deal = billable.find((d) => d.id === dealId) ?? null;
  const quote = quotes.find((q) => q.id === quoteId) ?? null;
  const lines: Product[] = quoteLines ?? deal?.products ?? [];
  const subtotal = lines.reduce((sum, p) => sum + Number(p.qty ?? 0) * Number(p.quoted_unit_price ?? 0), 0);
  const discount = !quote
    ? 0
    : quote.discount_percent != null && quote.discount_percent !== ""
      ? Math.min(subtotal, (subtotal * Number(quote.discount_percent)) / 100)
      : Math.min(subtotal, Number(quote.discount_amount ?? 0));
  const canSubmit = Boolean(customer) && Boolean(deal) && lines.length > 0 && !busy;

  async function create(issue: boolean) {
    if (!customer || !deal) return;
    if (issue) {
      const ok = await ask({
        action: copy.createAndIssue,
        target: customerName(customer),
        code: quote ? quote.quote_id : deal.deal_id,
        affects: [copy.createIssueAffects],
        reversible: copy.createIssueKeeps,
        confirmLabel: copy.createAndIssue,
      });
      if (!ok) return;
    }
    setBusy(true);
    setProblem("");
    say(t.dashboard.working);
    try {
      const body: Record<string, unknown> = { note: note.trim() || null };
      if (quote) body.quote_id = quote.id;
      else body.deal_id = deal.id;
      const created = await fetch(`/api/phase2/licenses/${licenseId}/invoices`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      if (!created.ok) {
        const reason = await failureText(created);
        setProblem(reason);
        say(reason, "error");
        return;
      }
      let invoice = (await created.json()) as CreatedInvoice;
      if (!issue) {
        say(copy.createdDraft.replace("{code}", invoice.invoice_id), "ok");
        await onCreated(invoice);
        return;
      }
      const issued = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${invoice.id}/issue`, {
        method: "POST",
        headers,
      });
      if (!issued.ok) {
        // The draft exists; the slow-renderer sentence (round 20U) and
        // every other refusal come through failureText in the reader's
        // words, and the row's own sheet has the "ออกเอกสาร" button.
        say(copy.createdNotIssued.replace("{code}", invoice.invoice_id).replace("{reason}", await failureText(issued)), "error");
        await onCreated(invoice);
        return;
      }
      const result = (await issued.json()) as { invoice?: CreatedInvoice; generated_document_id?: string };
      invoice = { ...invoice, ...(result.invoice ?? {}), generated_document_id: result.generated_document_id ?? null };
      say(copy.created.replace("{code}", invoice.invoice_id), "ok");
      await onCreated(invoice);
    } catch (error) {
      const reason = error instanceof Error ? error.message : t.common.error;
      setProblem(reason);
      say(reason, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open={open} title={copy.createTitle} onClose={onClose}>
      <dl className="fields">
        <FieldRow label={copy.customer}>
          {(id) =>
            customer ? (
              <div className="picker-chosen">
                <span>
                  {customerName(customer)} <span className="code">{customer.customer_id}</span>
                </span>
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  disabled={busy}
                  onClick={() => {
                    setCustomer(null);
                    setDealId("");
                    setQuery("");
                  }}
                >
                  {t.common.change}
                </button>
              </div>
            ) : (
              <div className="picker">
                <input
                  id={id}
                  type="search"
                  autoComplete="off"
                  value={query}
                  placeholder={copy.customerSearchHint}
                  onChange={(event) => setQuery(event.target.value)}
                />
                {query.trim() !== "" && (
                  <ul className="picker-options">
                    {matches.length === 0 ? (
                      <li className="picker-empty">{searching ? t.dashboard.opening : copy.customerNoMatch}</li>
                    ) : (
                      matches.map((row) => (
                        <li key={row.id}>
                          <button type="button" onClick={() => setCustomer(row)}>
                            {customerName(row)}
                            {row.phone ? ` · ${row.phone}` : ""}
                          </button>
                        </li>
                      ))
                    )}
                  </ul>
                )}
              </div>
            )
          }
        </FieldRow>

        {customer && deals !== null && billable.length === 0 && (
          <div className="field-row">
            <dt>{copy.deal}</dt>
            <dd>
              {/* Said plainly, with the way forward: the customer page is
                  where a deal is opened, and a deal without lines is
                  finished on its own page. */}
              <p className="hint" style={{ margin: "0 0 8px" }}>
                {deals.some((d) => d.stage !== "lost") ? copy.noBillableDeals : copy.noDealsForCustomer}
              </p>
              {deals.some((d) => d.stage !== "lost") ? (
                <Link className="btn" href={`/liff/sales/deals/${deals.find((d) => d.stage !== "lost")?.id}`}>
                  {copy.openDealLink}
                </Link>
              ) : (
                <Link className="btn" href={`/liff/sales/customers/${customer.id}`}>
                  {copy.createDealLink}
                </Link>
              )}
            </dd>
          </div>
        )}

        {customer && billable.length > 0 && (
          <FieldRow label={copy.deal}>
            {(id) => (
              <select id={id} value={dealId} disabled={busy} onChange={(event) => setDealId(event.target.value)}>
                <option value="">{copy.dealPick}</option>
                {billable.map((d) => (
                  <option key={d.id} value={d.id}>
                    {copy.dealLine
                      .replace("{code}", d.deal_id)
                      .replace("{stage}", dealStageLabel(d.stage))
                      .replace("{count}", String(d.products?.length ?? 0))}
                  </option>
                ))}
              </select>
            )}
          </FieldRow>
        )}
        {customer && billable.length > 0 && <p className="hint">{copy.dealHint}</p>}

        {deal && quotes.length > 0 && (
          <FieldRow label={copy.quote}>
            {(id) => (
              <select id={id} value={quoteId} disabled={busy} onChange={(event) => setQuoteId(event.target.value)}>
                <option value="">{copy.quoteNone}</option>
                {quotes.map((q) => (
                  <option key={q.id} value={q.id}>
                    {q.quote_id} · {quoteStatusLabel(q.status)}
                  </option>
                ))}
              </select>
            )}
          </FieldRow>
        )}
        {deal && quotes.length > 0 && <p className="hint">{copy.quoteHint}</p>}

        {deal && (
          <FieldRow label={copy.noteField}>
            {(id) => (
              <textarea id={id} rows={2} value={note} disabled={busy} onChange={(event) => setNote(event.target.value)} />
            )}
          </FieldRow>
        )}
      </dl>

      {deal && (
        <section className="section">
          <div className="section-head">
            <h2>{copy.lines}</h2>
          </div>
          <p className="hint" style={{ padding: "0 16px" }}>
            {quote
              ? copy.linesFromQuote.replace("{code}", quote.quote_id)
              : copy.linesFromDeal.replace("{code}", deal.deal_id)}
          </p>
          <ul className="list">
            {lines.map((line) => (
              <li key={line.id} className="card">
                <div className="card-title">{line.product_name}</div>
                <div className="card-meta">
                  {Number(line.qty ?? 0)} × <span className="money-line">{money(line.quoted_unit_price)}</span>
                  {" = "}
                  <span className="money-line">{money(Number(line.qty ?? 0) * Number(line.quoted_unit_price ?? 0))}</span>
                </div>
              </li>
            ))}
          </ul>
          <div className="totals">
            <span>{copy.subtotal}</span>
            <strong>{money(subtotal)}</strong>
          </div>
          {discount > 0 && (
            <div className="totals">
              <span>{copy.discount}</span>
              <strong>− {money(discount)}</strong>
            </div>
          )}
          <p className="hint" style={{ padding: "0 16px" }}>{copy.vatNote}</p>
        </section>
      )}

      {problem && (
        <p className="hint" role="alert">
          {problem}
        </p>
      )}

      <div className="actions">
        <button type="button" className="btn" data-variant="quiet" disabled={busy} onClick={onClose}>
          {t.common.cancel}
        </button>
        <button type="button" className="btn" disabled={!canSubmit} onClick={() => void create(false)}>
          {copy.createDraft}
        </button>
        <button type="button" className="btn" data-variant="primary" disabled={!canSubmit} onClick={() => void create(true)}>
          {busy ? t.dashboard.working : copy.createAndIssue}
        </button>
      </div>
      {customer && !deal && billable.length > 0 && <p className="hint">{copy.pickDealFirst}</p>}
      {!customer && <p className="hint">{copy.pickCustomerFirst}</p>}

      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={busy} />
    </Sheet>
  );
}
