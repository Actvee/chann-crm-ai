"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Empty } from "../../_components";
import { proxyHeaders } from "../../_lib";
import { useSalesSession } from "../../_session";
import { SalesShell } from "../../_shell";
import { useSalesText } from "../../_strings";

/**
 * Round 20V — what the customers said.
 *
 * Surveys have gone out and come back since Phase 14 and no screen ever
 * read them (owner's gap list, 21 ก.ย. 2569). GET surveys/summary is the
 * one query behind this page and chat's "คะแนนความพึงพอใจ".
 *
 * Form (dataviz): three headline numbers are stat tiles, not a chart; the
 * per-technician figures are a table whose rows carry one bar each in the
 * OA's accent — a single series, so no legend, and the number sits beside
 * the bar in text ink. The 1–3 spread is one hue at three steps with its
 * counts written out, so colour is never the only carrier.
 */
type Technician = {
  target_type: string;
  target_ref: string;
  display_name: string;
  answered: number;
  average: number | null;
  distribution: Record<string, number>;
};
type Recent = {
  survey_id: string;
  ticket_id: string;
  ticket_number: string;
  customer_name?: string | null;
  score: number | null;
  score_label?: string | null;
  comment?: string | null;
  submitted_at?: string | null;
  technician_name?: string | null;
};
type Summary = {
  days: number;
  scale: Record<string, string>;
  answered: number;
  pending: number;
  average: number | null;
  response_rate: number | null;
  distribution: Record<string, number>;
  technicians: Technician[];
  recent: Recent[];
};

const PERIODS = [30, 90, 365] as const;
// One hue, three steps (dataviz: sequential = light→dark of one colour).
const STEP_OPACITY: Record<string, number> = { "1": 0.35, "2": 0.65, "3": 1 };

export default function Satisfaction({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const s = useSalesText();
  const copy = s.satisfaction;
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [allowed, setAllowed] = useState<boolean | null>(null);

  const say = useCallback((text: string, next?: "ok" | "error") => {
    setStatus(text);
    setTone(next);
  }, []);
  const session = useSalesSession(liffId, say);
  const { token, licenseId } = session;

  useEffect(() => {
    if (!session.ready) return;
    setAllowed(session.permissions.has("view_reports"));
  }, [session.ready, session.permissions]);

  useEffect(() => {
    if (!session.ready || !token || !licenseId || allowed !== true) return;
    let live = true;
    say(t.dashboard.opening);
    void (async () => {
      try {
        const response = await fetch(
          `/api/phase2/licenses/${licenseId}/surveys/summary?days=${days}`,
          { headers: proxyHeaders(token, licenseId) },
        );
        if (!live) return;
        if (!response.ok) {
          say(response.status === 403 ? copy.noPermission : `${t.dashboard.loadFailed} (${response.status})`, "error");
          return;
        }
        setSummary((await response.json()) as Summary);
        say("");
      } catch {
        if (live) say(t.dashboard.loadFailed, "error");
      }
    })();
    return () => {
      live = false;
    };
  }, [session.ready, token, licenseId, allowed, days, say, t, copy.noPermission]);

  const periodLabel = (d: number) => (d === 30 ? copy.days30 : d === 90 ? copy.days90 : copy.days365);
  const scale = summary?.scale ?? {};
  const keys = Object.keys(scale).sort();
  const asked = (summary?.answered ?? 0) + (summary?.pending ?? 0);
  const peak = Math.max(1, ...(summary?.technicians ?? []).map((row) => row.average ?? 0));
  const when = (iso: string | null | undefined) =>
    iso
      ? new Date(iso).toLocaleDateString(locale === "en" ? "en-GB" : "th-TH", {
          timeZone: "Asia/Bangkok", day: "numeric", month: "short",
        })
      : "";

  return (
    <SalesShell
      session={session}
      title={copy.title}
      liffId={liffId}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="hint" style={{ marginBottom: 12 }}>{copy.intro}</p>

      {allowed === false && <Empty message={copy.noPermission} />}

      {allowed && (
        <>
          {/* The period, as pressed buttons rather than a select: three
              choices fit a row, and aria-pressed tells a reader which is
              on (ui-ux-pro-max, Compact Control Semantics). */}
          <div className="chat-row-chips" role="group" aria-label={copy.period} style={{ marginBottom: 14 }}>
            {PERIODS.map((d) => (
              <button
                key={d}
                type="button"
                className="chip"
                data-tone={days === d ? "live" : undefined}
                aria-pressed={days === d}
                onClick={() => setDays(d)}
              >
                {periodLabel(d)}
              </button>
            ))}
          </div>

          {summary && (
            <>
              <section className="pipeline" aria-label={copy.title}>
                <div className="pipeline-figures">
                  <div>
                    <span className="pipeline-value">{summary.average === null ? "—" : summary.average.toFixed(2)}</span>
                    <span className="pipeline-label">{copy.average} · {copy.outOf}</span>
                  </div>
                  <div>
                    <span className="pipeline-value">{summary.answered}</span>
                    <span className="pipeline-label">{copy.answered}</span>
                  </div>
                  <div>
                    <span className="pipeline-value">
                      {summary.response_rate === null ? "—" : `${Math.round(summary.response_rate * 100)}%`}
                    </span>
                    <span className="pipeline-label">
                      {copy.responseRate} · {copy.responseHint.replace("{answered}", String(summary.answered)).replace("{sent}", String(asked))}
                    </span>
                  </div>
                </div>
              </section>

              {summary.answered === 0 ? (
                <Empty
                  message={
                    copy.noAnswers + (summary.pending ? ` · ${copy.pendingNote.replace("{n}", String(summary.pending))}` : "")
                  }
                />
              ) : (
                <>
                  <section className="section" style={{ marginBottom: 16 }}>
                    <div className="section-head">
                      <h2>{copy.distribution}</h2>
                    </div>
                    {/* One bar, three segments of one hue, a 2px gap between
                        them, and the counts written beside — never colour
                        alone. */}
                    <div style={{ display: "flex", gap: 2, height: 10, borderRadius: 5, overflow: "hidden" }} aria-hidden="true">
                      {keys.map((key) => {
                        const n = summary.distribution[key] ?? 0;
                        if (!n) return null;
                        return (
                          <span
                            key={key}
                            style={{ flex: n, background: "var(--accent)", opacity: STEP_OPACITY[key] ?? 1 }}
                          />
                        );
                      })}
                    </div>
                    <dl className="fields" style={{ marginTop: 10 }}>
                      {keys.map((key) => (
                        <div className="field" key={key}>
                          <span>
                            <span
                              aria-hidden="true"
                              style={{ display: "inline-block", width: 10, height: 10, borderRadius: 5, marginRight: 8, background: "var(--accent)", opacity: STEP_OPACITY[key] ?? 1 }}
                            />
                            {key} · {scale[key]}
                          </span>
                          <strong>{summary.distribution[key] ?? 0}</strong>
                        </div>
                      ))}
                    </dl>
                  </section>

                  <section className="card report-card" style={{ marginBottom: 16 }}>
                    <h2 className="card-title">{copy.byTechnician}</h2>
                    {summary.technicians.length === 0 ? (
                      <p className="card-meta">{copy.noTechnicians}</p>
                    ) : (
                      <table className="sample-table report-table">
                        <thead>
                          <tr>
                            <th>{copy.technician}</th>
                            <th className="num">{copy.average}</th>
                            <th className="num">{copy.answers}</th>
                            <th className="bar-col" aria-hidden="true"></th>
                          </tr>
                        </thead>
                        <tbody>
                          {summary.technicians.map((row) => (
                            <tr key={`${row.target_type}:${row.target_ref}`}>
                              <td>{row.display_name}</td>
                              <td className="num">{row.average === null ? "—" : row.average.toFixed(2)}</td>
                              <td className="num">{row.answered}</td>
                              <td className="bar-col">
                                <span
                                  className="report-bar"
                                  title={keys.map((k) => `${scale[k]} ${row.distribution[k] ?? 0}`).join(" · ")}
                                  style={{ width: `${Math.max(2, Math.round((100 * (row.average ?? 0)) / peak))}%` }}
                                />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </section>

                  <div className="section-head">
                    <h2>{copy.recent}</h2>
                  </div>
                  <ul className="list">
                    {summary.recent.map((row) => (
                      <li key={row.survey_id} className="card">
                        <Link className="row-link" href={`/liff/sales/tickets/${row.ticket_id}`}>
                          <span className="row-body">
                            <span className="card-title">
                              <span className="code">{row.ticket_number}</span>
                              {" "}
                              <span className="chip" data-tone={row.score === 3 ? "live" : row.score === 1 ? "late" : undefined}>
                                {row.score} · {row.score_label ?? scale[String(row.score)] ?? ""}
                              </span>
                            </span>
                            <span className="card-meta">
                              {[row.customer_name, row.technician_name, when(row.submitted_at)].filter(Boolean).join(" · ")}
                            </span>
                            {row.comment && <span className="card-meta">“{row.comment}”</span>}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
          <p className="footnote">{copy.chatHint}</p>
        </>
      )}
    </SalesShell>
  );
}
