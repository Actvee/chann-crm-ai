"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell, Badge } from "../../_components";
import { ProductLineForm } from "../../../_product-line-form";
import { RecordHead, RelatedHeading } from "../../_record";
import { initLiffSession, openExternal, proxyHeaders } from "../../_lib";

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
  };
  deal: { id: string; deal_id: string; stage: string; products?: Product[] } | null;
  customer: {
    id: string;
    customer_id: string;
    first_name?: string | null;
    last_name?: string | null;
  } | null;
};

function money(value: unknown): string {
  const n = Number(value ?? 0);
  return Number.isFinite(n)
    ? n.toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";
}

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
  const { t } = useLanguage();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [lines, setLines] = useState<Product[]>([]);
  const [adding, setAdding] = useState(false);
  const [editingLine, setEditingLine] = useState<string>("");
  const [editingTerms, setEditingTerms] = useState(false);
  const [terms, setTerms] = useState({ valid_until: "", discount: "" });
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [licenseId, setLicenseId] = useState("");
  const [token, setToken] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      setToken(session.token);
      setLicenseId(license);
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      const response = await fetch(
        `/api/phase2/licenses/${license}/quotes/${quoteId}`,
        { headers: proxyHeaders(session.token, license) },
      );
      if (!response.ok) {
        say(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
          "error",
        );
        return;
      }
      setDetail((await response.json()) as Detail);
      await loadLines(session.token, license);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, quoteId, say, t]);

  async function openDocument() {
    if (!detail?.quote.generated_document_id) return;
    say(t.dashboard.working);
    try {
      // Ask for a signed link and hand it straight to the browser. The
      // previous version fetched the PDF as a blob and rendered a second
      // button pointing at a blob: URL — which LINE refuses to open, and
      // which made the person press twice to reach a dead end.
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/documents/${detail.quote.generated_document_id}/link`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      const { url } = (await response.json()) as { url: string };
      openExternal(url);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    }
  }

  // The QUOTE's lines, not the deal's. They were copied at creation and
  // are independent since, so showing the deal's would display something
  // other than what this document actually says.
  const loadLines = useCallback(
    async (currentToken = token, license = licenseId) => {
      const response = await fetch(
        `/api/phase2/licenses/${license}/quotes/${quoteId}/products`,
        { headers: proxyHeaders(currentToken, license) },
      );
      if (response.ok) setLines((await response.json()) as Product[]);
    },
    [licenseId, quoteId, token],
  );

  async function setQuoteStatus(status: string) {
    const label =
      (t.quote.status as Record<string, string>)[status] ?? status;
    if (!window.confirm(t.dashboard.quotes.confirmStatus.replace("{status}", label)))
      return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quoteId}/status`,
        {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({ status }),
        },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      say(t.dashboard.saved, "ok");
      await initialize();
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
          response.status === 409
            ? t.dashboard.quotes.issuedLocked
            : `${t.common.error} (${response.status})`,
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
          response.status === 409
            ? t.dashboard.quotes.issuedLocked
            : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      setEditingTerms(false);
      await initialize();
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
          response.status === 409
            ? t.dashboard.quotes.issuedLocked
            : `${t.common.error} (${response.status})`,
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
          response.status === 409
            ? t.dashboard.quotes.issuedLocked
            : `${t.common.error} (${response.status})`,
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

  const items = lines;
  // Only a draft. An issued quote is a document the customer is holding,
  // and the Data Tier refuses to change one — offering the buttons anyway
  // would mean every tap ends in a 409.
  const editable = detail?.quote.status === "draft";
  const subtotal = items.reduce(
    (sum, p) => sum + Number(p.qty ?? 0) * Number(p.quoted_unit_price ?? 0),
    0,
  );

  return (
    <AppShell
      title={detail?.quote.quote_id ?? t.quote.title}
      back="/liff/sales/quotes"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {detail && (
        <>
          <RecordHead
            stage={detail.quote.status}
            title={detail.quote.quote_id}
            badge={
              <Badge
                stage={detail.quote.status}
                label={
                  (t.quote.status as Record<string, string>)[detail.quote.status] ??
                  detail.quote.status
                }
              />
            }
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
                {/* A quote issued with the wrong contents cannot be
                    edited — the customer is holding it — so the only
                    honest path is to void this one and issue another.
                    Without these there was no way to do the first half
                    and the wrong quote stayed "sent" forever. */}
                {detail.quote.status !== "accepted" &&
                  detail.quote.status !== "rejected" && (
                    <>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => void setQuoteStatus("accepted")}
                        disabled={busy}
                      >
                        {t.dashboard.quotes.markAccepted}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        onClick={() => void setQuoteStatus("rejected")}
                        disabled={busy}
                      >
                        {t.dashboard.quotes.markRejected}
                      </button>
                    </>
                  )}
                {detail.quote.generated_document_id ? (
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => void openDocument()}
                  >
                    {t.dashboard.quotes.view}
                  </button>
                ) : null}
              </>
            }
          />

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
              <div className="field-row">
                <dt>{t.dashboard.quotes.validUntil}</dt>
                <dd>
                  {editingTerms ? (
                    <input
                      type="date"
                      value={terms.valid_until}
                      onChange={(event) =>
                        setTerms({ ...terms, valid_until: event.target.value })
                      }
                    />
                  ) : (
                    String(detail.quote.valid_until ?? "—")
                  )}
                </dd>
              </div>
              <div className="field-row">
                <dt>{t.dashboard.quotes.discount}</dt>
                <dd>
                  {editingTerms ? (
                    <input
                      value={terms.discount}
                      placeholder={t.dashboard.quotes.discountHint}
                      onChange={(event) =>
                        setTerms({ ...terms, discount: event.target.value })
                      }
                    />
                  ) : detail.quote.discount_percent ? (
                    `${detail.quote.discount_percent}%`
                  ) : detail.quote.discount_amount ? (
                    money(detail.quote.discount_amount)
                  ) : (
                    "—"
                  )}
                </dd>
              </div>
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
            </>
          )}
        </>
      )}
    </AppShell>
  );
}
