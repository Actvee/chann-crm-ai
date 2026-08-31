"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell, Badge } from "../../_components";
import { RecordHead, RelatedHeading } from "../../_record";
import { initLiffSession, proxyHeaders } from "../../_lib";

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
  const [docUrl, setDocUrl] = useState("");
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
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, quoteId, say, t]);

  async function openDocument() {
    if (!detail?.quote.generated_document_id) return;
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/documents/${detail.quote.generated_document_id}`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      // A link the person taps, not window.open — a popup call after an
      // await is not user-initiated and browsers block it silently.
      setDocUrl(URL.createObjectURL(await response.blob()));
      say(t.dashboard.quotes.ready, "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    }
  }

  const items = detail?.deal?.products ?? [];
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
              detail.quote.generated_document_id ? (
                docUrl ? (
                  <a
                    className="btn"
                    data-variant="primary"
                    href={docUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {t.dashboard.quotes.open}
                  </a>
                ) : (
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => void openDocument()}
                  >
                    {t.dashboard.quotes.view}
                  </button>
                )
              ) : null
            }
          />

          <RelatedHeading title={t.product.title} count={items.length} />
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
