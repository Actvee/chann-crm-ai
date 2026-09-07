"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Badge } from "../../_components";
import { FieldRow } from "../../../_field-row";
import { shortDate } from "../../../_list-controls";
import { ProductLineForm } from "../../../_product-line-form";
import { useFailureText, useFormatters } from "../../_format";
import { RecordHead, RelatedHeading } from "../../_record";
import { RelatedActivity } from "../../_related";
import { openExternal, proxyHeaders } from "../../_lib";
import { useSalesSession } from "../../_session";
import { SalesShell } from "../../_shell";
import { useSalesText } from "../../_strings";

type Product = {
  id: string;
  product_name?: string | null;
  qty?: number | string | null;
  quoted_unit_price?: string | number | null;
};

type Detail = {
  quote: {
    id: string;
    quote_id: string;
    status: string;
    deal_id: string;
    generated_document_id?: string | null;
    // Since migration 0020. A quote with no expiry is a price the shop
    // is bound to indefinitely.
    valid_until?: string | null;
    discount_percent?: string | null;
    discount_amount?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
  };
  deal: { id: string; deal_id: string; stage: string; products?: Product[] } | null;
  customer: {
    id: string;
    customer_id: string;
    first_name?: string | null;
    last_name?: string | null;
  } | null;
};

// The quote state machine, as phase10.py's _QUOTE_ALLOWED_TRANSITIONS
// has it (review C6): a draft is sent or dropped; a sent offer is
// answered or lapses; the rest is history. The page used to offer
// "ยอมรับ" on a draft, which was a 409 every time.
const NEXT_STATUSES: Record<string, string[]> = {
  draft: ["sent", "rejected"],
  sent: ["accepted", "rejected", "expired"],
  accepted: [],
  rejected: [],
  expired: [],
};

/**
 * What is actually ON a quote.
 *
 * The list row shows a code and a status, which is almost nothing: the
 * question someone opens a quote to answer is what the customer is being
 * charged for. That lives on the deal, so this page joins the two.
 */
export default function QuoteDetail({
  liffId,
  quoteId,
}: {
  liffId: string;
  quoteId: string;
}) {
  const { t, locale } = useLanguage();
  const s = useSalesText();
  const { money } = useFormatters();
  const failureText = useFailureText();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [lines, setLines] = useState<Product[]>([]);
  const [adding, setAdding] = useState(false);
  const [editingLine, setEditingLine] = useState<string>("");
  const [editingTerms, setEditingTerms] = useState(false);
  const [terms, setTerms] = useState({ valid_until: "", discount: "" });
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  // The QUOTE's lines, not the deal's. They were copied at creation and
  // are independent since, so showing the deal's would display something
  // other than what this document actually says.
  const loadLines = useCallback(async () => {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/products`,
      { headers: proxyHeaders(token, licenseId) },
    );
    if (!response.ok) {
      // A failed fetch must not render as "no line items" on a quote
      // that has them — the empty state and the error are different facts.
      say(`${t.dashboard.loadFailed} (${response.status})`, "error");
      return;
    }
    setLines((await response.json()) as Product[]);
  }, [licenseId, quoteId, say, t, token]);

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/quotes/${quoteId}`,
      { headers: proxyHeaders(token, licenseId) },
    );
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    setDetail((await response.json()) as Detail);
    await loadLines();
    say("");
  }, [licenseId, loadLines, quoteId, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [session.ready, load, say, t]);

  async function openDocument(documentId?: string) {
    const id = documentId ?? detail?.quote.generated_document_id;
    if (!id) return;
    say(t.dashboard.working);
    setBusy(true);
    try {
      // Ask for a signed link and hand it straight to the browser. The
      // previous version fetched the PDF as a blob and rendered a second
      // button pointing at a blob: URL — which LINE refuses to open, and
      // which made the person press twice to reach a dead end.
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/documents/${id}/link`,
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
    } finally {
      setBusy(false);
    }
  }

  /** Render, store and record the document — the same call the list
   *  page makes. It was missing here (review C6), so the page that
   *  shows what is on a quote could not issue it. */
  async function issue() {
    if (!detail) return;
    const already = Boolean(detail.quote.generated_document_id);
    if (
      !window.confirm(
        already
          ? t.dashboard.quotes.confirmReissue.replace("{code}", detail.quote.quote_id)
          : t.dashboard.quotes.confirmIssue.replace("{code}", detail.quote.quote_id),
      )
    ) {
      return;
    }
    setBusy(true);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/issue?allow_reissue=${already}`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const issued = (await response.json()) as { sha256?: string; generated_document_id?: string };
      say(
        `${detail.quote.quote_id} — ${t.dashboard.quotes.issued} · SHA-256 ${(issued.sha256 ?? "").slice(0, 12)}…`,
        "ok",
      );
      await load();
      if (issued.generated_document_id) await openDocument(String(issued.generated_document_id));
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusy(false);
    }
  }

  async function setQuoteStatus(next: string) {
    const label = statusLabel(next);
    if (!window.confirm(t.dashboard.quotes.confirmStatus.replace("{status}", label)))
      return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/status`,
        {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({ status: next }),
        },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      say(t.dashboard.saved, "ok");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function updateLine(
    lineId: string, line: { name: string; qty: number; price: string },
  ) {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/products/${lineId}`,
        {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            product_name: line.name,
            quoted_unit_price: line.price,
            qty: line.qty,
          }),
        },
      );
      if (!response.ok) {
        say(
          response.status === 409 ? t.dashboard.quotes.issuedLocked : await failureText(response),
          "error",
        );
        return;
      }
      setEditingLine("");
      await loadLines();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function saveTerms() {
    setBusy(true);
    try {
      const body: Record<string, string> = {};
      if (terms.valid_until) body.valid_until = terms.valid_until;
      // Percent when it ends in %, an absolute amount otherwise — the
      // two are stored separately because "10% and also 500 off" is
      // ambiguous about which applies first.
      const discount = terms.discount.trim();
      if (discount) {
        if (discount.endsWith("%")) {
          body.discount_percent = discount.slice(0, -1).trim();
        } else {
          body.discount_amount = discount;
        }
      }
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/terms`,
        {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify(body),
        },
      );
      if (!response.ok) {
        say(
          response.status === 409 ? t.dashboard.quotes.issuedLocked : await failureText(response),
          "error",
        );
        return;
      }
      setEditingTerms(false);
      await load();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function addLine(line: { name: string; qty: number; price: string }) {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/products`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            product_name: line.name,
            quoted_unit_price: line.price,
            qty: line.qty,
          }),
        },
      );
      if (!response.ok) {
        // 409 is the rule doing its job on an issued quote, not a fault.
        say(
          response.status === 409 ? t.dashboard.quotes.issuedLocked : await failureText(response),
          "error",
        );
        return;
      }
      setAdding(false);
      await loadLines();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function removeLine(line: Product) {
    if (!window.confirm(`${t.common.delete}: ${line.product_name ?? ""}?`)) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/products/${line.id}`,
        { method: "DELETE", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(
          response.status === 409 ? t.dashboard.quotes.issuedLocked : await failureText(response),
          "error",
        );
        return;
      }
      await loadLines();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  const statusLabel = (value: string) =>
    (t.quote.status as Record<string, string>)[value] ?? value;
  const actionLabel = (value: string) =>
    value === "sent"
      ? s.quotes.markSent
      : value === "expired"
        ? s.quotes.markExpired
        : value === "accepted"
          ? t.dashboard.quotes.markAccepted
          : t.dashboard.quotes.markRejected;

  const items = lines;
  const can = (key: string) => !session.suspended && permissions.has(key);
  // Every write on this page — terms, lines, status, issuing — is behind
  // quote.update, which the page never asked for (review C7).
  const canUpdate = can("quote.update");
  const quoteStatus = detail?.quote.status ?? "";
  // Only a draft. An issued quote is a document the customer is holding,
  // and the Data Tier refuses to change one — offering the buttons anyway
  // would mean every tap ends in a 409.
  const editable = canUpdate && quoteStatus === "draft";
  const moves = canUpdate ? NEXT_STATUSES[quoteStatus] ?? [] : [];
  const issuable = canUpdate && (quoteStatus === "draft" || quoteStatus === "sent");
  const subtotal = items.reduce(
    (sum, p) => sum + Number(p.qty ?? 0) * Number(p.quoted_unit_price ?? 0),
    0,
  );
  // The same arithmetic the document uses: discount off first, VAT on
  // what is left. A screen total that disagrees with the printed one is
  // the worst possible disagreement, so the two must share the rule.
  const discount = detail?.quote.discount_percent
    ? Math.min(subtotal, subtotal * Number(detail.quote.discount_percent) / 100)
    : detail?.quote.discount_amount
      ? Math.min(subtotal, Number(detail.quote.discount_amount))
      : 0;
  const net = subtotal - discount;

  return (
    <SalesShell
      session={session}
      title={detail?.quote.quote_id ?? t.quote.title}
      back="/liff/sales/quotes"
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {detail && (
        <>
          <RecordHead
            createdAt={detail.quote.created_at}
            updatedAt={detail.quote.updated_at}
            stage={detail.quote.status}
            title={detail.quote.quote_id}
            badge={<Badge stage={detail.quote.status} label={statusLabel(detail.quote.status)} />}
            subtitle={
              <>
                {detail.customer && (
                  <Link href={`/liff/sales/customers/${detail.customer.id}`}>
                    {[detail.customer.first_name, detail.customer.last_name]
                      .filter(Boolean)
                      .join(" ")}
                  </Link>
                )}
                {detail.deal && (
                  <>
                    {" · "}
                    <Link href={`/liff/sales/deals/${detail.deal.id}`}>
                      {detail.deal.deal_id}
                    </Link>
                  </>
                )}
              </>
            }
            actions={
              <>
                {issuable && (
                  <button
                    type="button"
                    className="btn"
                    data-variant={detail.quote.generated_document_id ? undefined : "primary"}
                    onClick={() => void issue()}
                    disabled={busy}
                  >
                    {detail.quote.generated_document_id
                      ? t.dashboard.quotes.reissue
                      : t.dashboard.quotes.issue}
                  </button>
                )}
                {/* The moves the state machine allows from here, and
                    only those. Voiding a wrong quote is "rejected": it
                    cannot be edited once issued, so the honest path is
                    to drop this one and issue a replacement. */}
                {moves.map((next) => (
                  <button
                    key={next}
                    type="button"
                    className="btn"
                    data-variant={next === "accepted" ? "primary" : "quiet"}
                    onClick={() => void setQuoteStatus(next)}
                    disabled={busy}
                  >
                    {actionLabel(next)}
                  </button>
                ))}
                {detail.quote.generated_document_id ? (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => void openDocument()}
                    disabled={busy}
                  >
                    {busy ? t.dashboard.working : t.dashboard.quotes.view}
                  </button>
                ) : null}
              </>
            }
          />

          {quoteStatus === "draft" && canUpdate && !detail.quote.generated_document_id && (
            <p className="card-meta" style={{ marginBottom: 14 }}>{s.quotes.issueBeforeSend}</p>
          )}
          {!canUpdate && !session.suspended && (
            <p className="card-meta" style={{ marginBottom: 14 }}>{s.quotes.needsUpdate}</p>
          )}
          {(NEXT_STATUSES[quoteStatus] ?? []).length === 0 && (
            <p className="card-meta" style={{ marginBottom: 14 }}>{s.quotes.final}</p>
          )}

          {/* When the offer stops standing, and what came off the price.
              Both were storable from migration 0020 and reachable from
              nowhere — a quote's validity could be defaulted but never
              changed. */}
          <section className="section" style={{ marginBottom: 14 }}>
            <div className="section-head">
              <h2>{t.dashboard.quotes.terms}</h2>
              {editable && !editingTerms && (
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => {
                    setTerms({
                      valid_until: String(detail.quote.valid_until ?? ""),
                      discount: detail.quote.discount_percent
                        ? `${detail.quote.discount_percent}%`
                        : String(detail.quote.discount_amount ?? ""),
                    });
                    setEditingTerms(true);
                  }}
                >
                  {t.common.edit}
                </button>
              )}
            </div>
            <dl className="fields">
              <FieldRow label={t.dashboard.quotes.validUntil}>
                {editingTerms
                  ? (id) => (
                      <input
                        id={id}
                        type="date"
                        value={terms.valid_until}
                        onChange={(event) =>
                          setTerms({ ...terms, valid_until: event.target.value })
                        }
                      />
                    )
                  : shortDate(detail.quote.valid_until, locale) || "—"}
              </FieldRow>
              <FieldRow label={t.dashboard.quotes.discount}>
                {editingTerms
                  ? (id) => (
                      <input
                        id={id}
                        value={terms.discount}
                        placeholder={t.dashboard.quotes.discountHint}
                        onChange={(event) =>
                          setTerms({ ...terms, discount: event.target.value })
                        }
                      />
                    )
                  : detail.quote.discount_percent
                    ? `${detail.quote.discount_percent}%`
                    : detail.quote.discount_amount
                      ? money(detail.quote.discount_amount)
                      : "—"}
              </FieldRow>
              {editingTerms && (
                <div className="actions">
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    onClick={() => setEditingTerms(false)}
                    disabled={busy}
                  >
                    {t.common.cancel}
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => void saveTerms()}
                    disabled={busy}
                  >
                    {busy ? t.dashboard.saving : t.common.save}
                  </button>
                </div>
              )}
            </dl>
          </section>

          {/* Why the controls are gone, said out loud.
              Everything on this page — terms, line items, notes — is
              editable only while the quote is a draft, which is correct:
              an issued quote is a document someone has been sent. But the
              page simply hid the buttons, so it read as broken rather
              than as a rule (reported 2 Sep). */}
          {quoteStatus !== "draft" && (
            <p className="card-meta" style={{ marginBottom: 14 }}>
              {t.dashboard.quotes.issuedReadOnly}
            </p>
          )}

          <RelatedHeading title={t.product.title} count={items.length} />
          {editable && (
            <section className="section" style={{ margin: "0 0 14px" }}>
              <div className="section-head">
                <h2>{t.dashboard.deals.addProduct}</h2>
                {!adding && (
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => setAdding(true)}
                  >
                    {t.dashboard.deals.addProduct}
                  </button>
                )}
              </div>
              {adding && (
                <ProductLineForm
                  licenseId={licenseId}
                  token={token}
                  busy={busy}
                  onCancel={() => setAdding(false)}
                  onSubmit={addLine}
                />
              )}
            </section>
          )}
          {items.length === 0 ? (
            <div className="empty">
              {/* A quote can no longer be created without line items, but
                  older ones may predate that rule. */}
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
                          Number(product.qty ?? 0) * Number(product.quoted_unit_price ?? 0),
                        )}
                      </strong>
                    </div>
                    {editable && editingLine !== product.id && (
                      <div className="card-actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          onClick={() => setEditingLine(product.id)}
                          disabled={busy}
                        >
                          {t.common.edit}
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          onClick={() => void removeLine(product)}
                          disabled={busy}
                        >
                          {t.common.delete}
                        </button>
                      </div>
                    )}
                    {editable && editingLine === product.id && (
                      <ProductLineForm
                        licenseId={licenseId}
                        token={token}
                        busy={busy}
                        initial={{
                          name: String(product.product_name ?? ""),
                          qty: Number(product.qty ?? 1),
                          price: String(product.quoted_unit_price ?? ""),
                        }}
                        onCancel={() => setEditingLine("")}
                        onSubmit={(line) => updateLine(product.id, line)}
                      />
                    )}
                  </li>
                ))}
              </ul>
              <div className="totals">
                <span>{t.dashboard.deals.subtotal}</span>
                <strong>{money(subtotal)}</strong>
              </div>
              {discount > 0 && (
                <>
                  <div className="totals">
                    <span>{t.dashboard.quotes.discount}</span>
                    <strong>-{money(discount)}</strong>
                  </div>
                  <div className="totals">
                    <span>{t.dashboard.quotes.net}</span>
                    <strong>{money(net)}</strong>
                  </div>
                </>
              )}
            </>
          )}

          <RelatedActivity
            licenseId={licenseId}
            token={token}
            entityType="quote"
            entityId={quoteId}
            permissions={permissions}
            readOnly={session.suspended}
          />
        </>
      )}
    </SalesShell>
  );
}
