"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell, Badge, CompanyPicker, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { InlineCreateForm } from "../../_inline-create";

import { Membership, fetchPermissions, initLiffSession, openExternal, proxyHeaders } from "../_lib";

type Quote = {
  id: string;
  quote_id: string;
  status: string;
  generated_document_id?: string | null;
};

export default function QuoteList({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const statusLabel = (status: string) =>
    (t.quote.status as Record<string, string>)[status] ?? status;
  const [token, setToken] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [licenseId, setLicenseId] = useState("");
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");
  const [busy, setBusy] = useState(false);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [openDeals, setOpenDeals] = useState<{ id: string; label: string }[]>([]);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

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
  }, [licenseId, say, token]);

  useEffect(() => {
    if (!token || !licenseId) return;
    void load().catch((error: unknown) =>
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error"),
    );
  }, [licenseId, load, say, token]);


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
  }, [liffId, say]);

  async function detail(response: Response): Promise<string> {
    try {
      return ((await response.clone().json()) as { detail?: string }).detail ?? "";
    } catch {
      return "";
    }
  }



  async function openDocument(quote: Quote, documentId?: string) {
    const id = documentId ?? quote.generated_document_id;
    if (!id) return;
    say(t.dashboard.working);
    try {
      // A signed https link, opened by the browser. Fetching the PDF as a
      // blob and linking to blob: cannot work here: LINE refuses those
      // URLs, and it made the person press twice to reach a dead end.
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/documents/${id}/link`,
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

  async function createQuote(values: Record<string, string>) {
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/quotes`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify(values),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        // "No products" is the common refusal and is actionable, so it
        // is passed through rather than flattened into a status code.
        const message =
          typeof detail.detail === "string" &&
          detail.detail.toLowerCase().includes("no products")
            ? t.dashboard.quotes.dealHasNoProducts
            : `${t.common.error} (${response.status})`;
        say(message, "error");
        return;
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
      const reason = await detail(response);
      if (!response.ok) {
        say(
          response.status === 409
            ? `${t.dashboard.quotes.incomplete} ${reason}`
            : `${t.common.error} (${response.status}) ${reason}`,
          "error",
        );
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

  return (
    <AppShell
      title={t.quote.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <CompanyPicker memberships={memberships} licenseId={licenseId} onChange={setLicenseId} />

      <Count shown={quotes.length} total={quotes.length} />

      {permissions.has("quote.create") && openDeals.length > 0 && (
        <InlineCreateForm
          title={t.dashboard.quotes.add}
          busy={busy}
          fields={[
            {
              name: "deal_id",
              label: t.deal.title,
              required: true,
              type: "select",
              options: openDeals.map((d) => ({ value: d.id, label: d.label })),
            },
          ]}
          onSubmit={createQuote}
        />
      )}

      {quotes.length === 0 ? (
        <Empty message={t.dashboard.quotes.empty} />
      ) : (
        <ul className="list">
          {quotes.map((quote) => (
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
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="footnote">{t.dashboard.quotes.note}</p>
    </AppShell>
  );
}
