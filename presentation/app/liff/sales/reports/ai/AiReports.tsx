"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../../_components";
import { fetchPermissions, initLiffSession, openExternal, proxyHeaders } from "../../_lib";

type Row = { key: string; label: string; value: number };
type Result = {
  entity: string;
  metric: string;
  group_by: string | null;
  date_range: string | null;
  rows: Row[];
  total: number | null;
};
type Answer = {
  clarify?: string;
  error?: string;
  message?: string;
  spec?: Record<string, unknown>;
  result?: Result;
  text?: string;
  files?: { csv?: string | null; html?: string | null; pdf?: string | null };
};

/** Phase 17 — the report viewer. One question box, the model turns it
 *  into a whitelisted spec, the numbers come back as a table with bars. */
export default function AiReports({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const copy = t.dashboard.aiReports;
  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);

  const say = useCallback((text: string, next?: "ok" | "error") => {
    setStatus(text);
    setTone(next);
  }, []);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setToken(session.token);
      setLicenseId(license);
      const permissions = await fetchPermissions(session.token, license);
      setAllowed(permissions.has("view_reports"));
      say("");
    } catch {
      say(t.liff.sdkLoadFailed, "error");
    }
  }, [liffId, say, t]);

  async function ask(text: string) {
    const message = text.trim();
    if (!message || !token || !licenseId) return;
    setBusy(true);
    setAnswer(null);
    say(copy.working);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/reports/ai`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ message, language: locale }),
      });
      if (response.status === 503) {
        say(copy.unavailable, "error");
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      const data = (await response.json()) as Answer;
      setAnswer(data);
      say(data.clarify ? copy.clarify : data.error ? data.message ?? copy.failed : copy.done, data.error ? "error" : "ok");
    } catch {
      say(copy.failed, "error");
    } finally {
      setBusy(false);
    }
  }

  const rows = answer?.result?.rows ?? [];
  const peak = Math.max(1, ...rows.map((r) => r.value));

  return (
    <AppShell
      title={copy.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="page-intro">{copy.intro}</p>

      {allowed === false && <p className="callout">{copy.noPermission}</p>}

      {allowed !== false && (
        <form
          className="fields"
          onSubmit={(event) => {
            event.preventDefault();
            void ask(question);
          }}
        >
          <label className="field">
            {copy.askLabel}
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={copy.placeholder}
              rows={2}
              disabled={busy || !token}
            />
          </label>
          <div className="actions">
            <button type="submit" className="btn" disabled={busy || !token || !question.trim()}>
              {busy ? copy.working : copy.ask}
            </button>
          </div>
          <div className="chat-row-chips" aria-label={copy.examplesLabel}>
            {copy.examples.map((example) => (
              <button
                key={example}
                type="button"
                className="chip"
                disabled={busy || !token}
                onClick={() => {
                  setQuestion(example);
                  void ask(example);
                }}
              >
                {example}
              </button>
            ))}
          </div>
        </form>
      )}

      {answer?.clarify && (
        <section className="card">
          <h2 className="card-title">{copy.clarify}</h2>
          <p>{answer.clarify}</p>
        </section>
      )}

      {answer?.result && (
        <section className="card report-card">
          <h2 className="card-title">{answer.text?.split("\n")[0]}</h2>
          {answer.result.group_by ? (
            rows.length === 0 ? (
              <p className="empty">{copy.noData}</p>
            ) : (
              <table className="sample-table report-table">
                <thead>
                  <tr>
                    <th>{copy.group}</th>
                    <th className="num">{copy.value}</th>
                    <th className="bar-col" aria-hidden="true"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.key || row.label}>
                      <td>{row.label}</td>
                      <td className="num">{row.value.toLocaleString()}</td>
                      <td className="bar-col">
                        <span className="report-bar" style={{ width: `${Math.max(2, Math.round((100 * row.value) / peak))}%` }} />
                      </td>
                    </tr>
                  ))}
                  {answer.result.total !== null && (
                    <tr className="report-total">
                      <td>{copy.total}</td>
                      <td className="num">{answer.result.total.toLocaleString()}</td>
                      <td></td>
                    </tr>
                  )}
                </tbody>
              </table>
            )
          ) : (
            <p className="report-big">{(answer.result.total ?? 0).toLocaleString()}</p>
          )}
          {answer.files && (answer.files.csv || answer.files.html || answer.files.pdf) && (
            <div className="card-actions">
              {answer.files.csv && (
                <button type="button" className="card-button" onClick={() => openExternal(answer.files!.csv!)}>
                  {copy.downloadCsv}
                </button>
              )}
              {answer.files.pdf && (
                <button type="button" className="card-button" onClick={() => openExternal(answer.files!.pdf!)}>
                  {copy.downloadPdf}
                </button>
              )}
              {answer.files.html && (
                <button type="button" className="card-button" onClick={() => openExternal(answer.files!.html!)}>
                  {copy.openPage}
                </button>
              )}
            </div>
          )}
          <p className="footnote">{copy.footnote}</p>
        </section>
      )}
    </AppShell>
  );
}
