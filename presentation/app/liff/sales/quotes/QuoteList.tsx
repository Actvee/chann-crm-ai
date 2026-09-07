"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { InlineCreateForm } from "../../_inline-create";
import {
  ListControls, byNewest, byOldest, useListControls,
} from "../../_list-controls";

import { useFailureText } from "../_format";
import { openExternal, proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { useSalesText } from "../_strings";

type Quote = {
  created_at?: string | null;
  id: string;
  quote_id: string;
  status: string;
  generated_document_id?: string | null;
};

export default function QuoteList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const s = useSalesText();
  const failureText = useFailureText();
  const statusLabel = (status: string) =>
    (t.quote.status as Record<string, string>)[status] ?? status;
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");
  const [busy, setBusy] = useState(false);
  const [openDeals, setOpenDeals] = useState<{ id: string; label: string; keywords: string }[]>([]);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId, permissions } = session;

  const load = useCallback(async () => {
    if (!token || !licenseId) return;
    const response = await fetch(`/api/phase2/licenses/${licenseId}/quotes`, {
      headers: proxyHeaders(token, licenseId),
    });
    if (!response.ok) {
      throw new Error(
        response.status === 403
          ? t.dashboard.noPermission
          : `${t.dashboard.loadFailed} (${response.status})`,
      );
    }
    setQuotes((await response.json()) as Quote[]);
    say("");
  }, [licenseId, say, t, token]);

  const loadDeals = useCallback(async () => {
    if (!token || !licenseId || !permissions.has("quote.create")) return;
    const headers = proxyHeaders(token, licenseId);
    // A quote is created FROM a deal, and only an open one: quoting a
    // closed deal is not a thing. The deal row carries only contact_id,
    // so the customers are loaded alongside and the picker shows a name
    // (review C12) — nobody remembers a deal by its code.
    const [dealsResponse, customersResponse] = await Promise.all([
      fetch(`/api/phase2/licenses/${licenseId}/deals`, { headers }),
      fetch(`/api/phase2/licenses/${licenseId}/customers`, { headers }),
    ]);
    if (!dealsResponse.ok) {
      // Without the deals the create-quote form would silently not
      // appear, which reads as "you cannot create quotes". Say why.
      say(`${t.dashboard.loadFailed} (${dealsResponse.status})`, "error");
      return;
    }
    const names = new Map<string, string>();
    if (customersResponse.ok) {
      const customers = (await customersResponse.json()) as {
        id: string; first_name?: string | null; last_name?: string | null; customer_id?: string;
        phone?: string | null;
      }[];
      for (const c of customers) {
        names.set(
          c.id,
          [c.first_name, c.last_name].filter(Boolean).join(" ") || (c.customer_id ?? ""),
        );
      }
    }
    const rows = (await dealsResponse.json()) as {
      id: string; deal_id?: string; stage?: string; contact_id?: string;
    }[];
    setOpenDeals(
      rows
        .filter((row) => row.stage === "new" || row.stage === "proposed")
        .map((row) => {
          const name = names.get(row.contact_id ?? "") ?? "";
          return {
            id: row.id,
            label: `${row.deal_id ?? row.id}${name ? ` · ${name}` : ""}`,
            keywords: name,
          };
        }),
    );
  }, [licenseId, permissions, say, t, token]);

  useEffect(() => {
    if (!session.ready) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
    void loadDeals().catch(() => undefined);
  }, [session.ready, load, loadDeals, say, t]);

  async function openDocument(quote: Quote, documentId?: string) {
    const id = documentId ?? quote.generated_document_id;
    if (!id) return;
    say(t.dashboard.working);
    // The button reads busyId, so set it here — the old code only set it
    // in issue(), leaving the view button double-tappable.
    setBusyId(quote.id);
    try {
      // A signed https link, opened by the browser. Fetching the PDF as a
      // blob and linking to blob: cannot work here: LINE refuses those
      // URLs, and it made the person press twice to reach a dead end.
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
      setBusyId("");
    }
  }

  async function createQuote(values: Record<string, string>) {
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/quotes`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify(values),
      });
      if (!response.ok) {
        // "No products" is the common refusal and is actionable; the API
        // sends a reason code and the sentence is in the reader's
        // language. Thrown so the picker keeps its choice (C3).
        const why = await failureText(response);
        say(why, "error");
        throw new Error(why);
      }
      await load();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function issue(quote: Quote) {
    const already = Boolean(quote.generated_document_id);
    if (
      !window.confirm(
        already
          ? t.dashboard.quotes.confirmReissue.replace("{code}", quote.quote_id)
          : t.dashboard.quotes.confirmIssue.replace("{code}", quote.quote_id),
      )
    ) {
      return;
    }
    setBusyId(quote.id);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quote.id}/issue?allow_reissue=${already}`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        say(await failureText(response), "error");
        return;
      }
      const issued = (await response.json()) as {
        sha256?: string;
        generated_document_id?: string;
      };
      say(
        `${quote.quote_id} — ${t.dashboard.quotes.issued} · SHA-256 ${(issued.sha256 ?? "").slice(0, 12)}…`,
        "ok",
      );
      await load();

      // Fetch by DOCUMENT id, straight from the issue response, rather
      // than by quote id. Going back through the quote depends on the
      // quote→document link having been written and read back in time;
      // when that lagged or failed, the person who had just issued a
      // document was told there wasn't one.
      if (issued.generated_document_id) {
        await openDocument(quote, String(issued.generated_document_id));
      }
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  const sorts = [
    { key: "newest", label: t.dashboard.list.newest, compare: byNewest<Quote> },
    { key: "oldest", label: t.dashboard.list.oldest, compare: byOldest<Quote> },
    {
      key: "code",
      label: t.dashboard.list.byCode,
      compare: (a: Quote, b: Quote) => b.quote_id.localeCompare(a.quote_id),
    },
  ];
  const controls = useListControls(quotes, sorts, "newest");
  const visibleQuotes = controls.visible;
  const can = (key: string) => !session.suspended && permissions.has(key);
  // Issuing changes state, so it needs quote.update — the key the route
  // checks (review C7); the list offered it to anyone who could read.
  const canIssue = can("quote.update");
  // Only a quote that is still an offer gets a document: an accepted,
  // rejected or expired one is history (phase10's state machine).
  const issuable = (quote: Quote) => quote.status === "draft" || quote.status === "sent";

  return (
    <SalesShell
      session={session}
      title={t.quote.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
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

      <Count shown={visibleQuotes.length} total={quotes.length} />

      {can("quote.create") && openDeals.length > 0 && (
        <InlineCreateForm
          title={t.dashboard.quotes.add}
          busy={busy}
          fields={[
            {
              name: "deal_id",
              label: t.deal.title,
              required: true,
              type: "select",
              searchHint: t.dashboard.deals.searchHint,
              options: openDeals.map((d) => ({
                value: d.id, label: d.label, keywords: d.keywords,
              })),
            },
          ]}
          onSubmit={createQuote}
        />
      )}

      {visibleQuotes.length === 0 ? (
        <Empty message={t.dashboard.quotes.empty} />
      ) : (
        <ul className="list">
          {visibleQuotes.map((quote) => (
            <li key={quote.id} className="card" data-stage={quote.status}>
              <Link
                className="row-link"
                href={`/liff/sales/quotes/${quote.id}`}
              >
              <div className="card-title">
                <span className="code">{quote.quote_id}</span>
                <Badge stage={quote.status} label={statusLabel(quote.status)} />
              </div>
              <div className="card-meta">
                {quote.generated_document_id
                  ? t.dashboard.quotes.issued
                  : t.dashboard.quotes.notIssued}
              </div>
              </Link>
              <div className="card-actions">
                {quote.generated_document_id && (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => void openDocument(quote)}
                    disabled={busyId === quote.id}
                  >
                    {busyId === quote.id ? t.dashboard.working : t.dashboard.quotes.view}
                  </button>
                )}
                {canIssue && issuable(quote) && (
                  <button
                    type="button"
                    className="btn"
                    data-variant={quote.generated_document_id ? undefined : "primary"}
                    onClick={() => void issue(quote)}
                    disabled={busyId === quote.id}
                  >
                    {quote.generated_document_id
                      ? t.dashboard.quotes.reissue
                      : t.dashboard.quotes.issue}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {!canIssue && !session.suspended && <p className="footnote">{s.quotes.needsUpdate}</p>}
      <p className="footnote">{t.dashboard.quotes.note}</p>
    </SalesShell>
  );
}
