"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell, Badge, CompanyPicker, Count, Empty } from "../_components";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Membership, initLiffSession, proxyHeaders } from "../_lib";

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
  // Object URLs for documents already fetched, keyed by quote.
  const [docUrls, setDocUrls] = useState<Record<string, string>>({});

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

  // Object URLs hold the blob in memory until revoked; without this a
  // person opening several quotes leaks every one of them.
  useEffect(() => {
    return () => {
      for (const url of Object.values(docUrls)) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  async function openDocumentById(quoteId: string, documentId: string) {
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/documents/${documentId}`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) return;
      const url = URL.createObjectURL(await response.blob());
      setDocUrls((current) => {
        const previous = current[quoteId];
        if (previous) URL.revokeObjectURL(previous);
        return { ...current, [quoteId]: url };
      });
    } catch {
      // The document exists; only the immediate preview failed. The
      // "view document" button on the row is the fallback, so this must
      // not surface as a failure to issue.
    }
  }

  async function openDocument(quote: Quote) {
    setBusyId(quote.id);
    say(t.dashboard.working);
    try {
      // The stored file, not a fresh render. /pdf goes through SmartBrowz
      // every time and returns 503 whenever that provider is unavailable —
      // a strange failure to hit for a document that already exists and is
      // sitting in the bucket.
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/quotes/${quote.id}/document`,
        { headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) {
        const reason = await detail(response);
        say(
          response.status === 404
            ? t.dashboard.quotes.notIssued
            : `${t.common.error} (${response.status}) ${reason}`,
          "error",
        );
        return;
      }
      // Held in state and rendered as a link the person taps, NOT opened
      // with window.open. A popup call after an await is not treated as
      // user-initiated, so browsers block it silently — which is exactly
      // what "I pressed the button and nothing happened" was. The LINE
      // in-app browser is stricter about this than most.
      //
      // Still fetched as a blob rather than linked directly, because the
      // request needs LIFF auth headers that a plain anchor cannot send.
      const url = URL.createObjectURL(await response.blob());
      setDocUrls((current) => {
        const previous = current[quote.id];
        if (previous) URL.revokeObjectURL(previous);
        return { ...current, [quote.id]: url };
      });
      say(t.dashboard.quotes.ready, "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusyId("");
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
        await openDocumentById(quote.id, String(issued.generated_document_id));
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
              {docUrls[quote.id] && (
                <p style={{ margin: "10px 0 0" }}>
                  <a
                    className="btn"
                    data-variant="primary"
                    href={docUrls[quote.id]}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {t.dashboard.quotes.open}
                  </a>
                </p>
              )}
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
