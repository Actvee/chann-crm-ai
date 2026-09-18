"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { useSalesSession } from "../../_session";
import { SalesShell } from "../../_shell";
import { openExternal, proxyHeaders } from "../../_lib";

type Row = { key: string; label: string; value: number };
type Result = {
  entity: string;
  metric: string;
  group_by: string | null;
  date_range: string | null;
  rows: Row[];
  total: number | null;
};
type Option = { value: string; label: string };
type EntityOption = Option & {
  date_fields: Option[];
  group_by: Option[];
  numeric_fields: Option[];
};
type Options = { entities: EntityOption[]; metrics: Option[]; date_ranges: Option[] };
/** The spec the model produced — the same shape `/reports/ai/run` validates. */
type Spec = {
  entity: string;
  metric: string;
  field: string | null;
  filter: Record<string, string>;
  group_by: string | null;
  date_range: string | null;
  date_field: string;
};
type Answer = {
  clarify?: string;
  error?: string;
  message?: string;
  spec?: Spec;
  result?: Result;
  text?: string;
  files?: { csv?: string | null; html?: string | null; pdf?: string | null };
  /** Phase 17 ตาราง/กราฟ — the same numbers as a picture, the one form of
   *  this report a person can forward into a chat. Null when the document
   *  store is not configured or the result is a single number. */
  chart?: string | null;
  /** False when the result is ONE number, which has nothing to plot — a
   *  different fact from "the picture failed". The API has always sent it
   *  and the page threw it away, so a report with no chart looked broken
   *  rather than explained (owner, 18 ก.ย. 2569). */
  plottable?: boolean;
  /** Present when a picture was drawn: how many of the month's AI charts
   *  this shop has used. `allowed: false` means the month is spent and the
   *  image was withheld — the numbers and the files are unaffected. */
  quota?: { allowed: boolean; used: number; allowance: number; unknown: boolean };
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
  // The spec editor (round 20k): the model's answer is a starting point, not
  // the last word — `/reports/ai/run` existed with no caller, so a person who
  // wanted the same report over a different range had to re-type the question
  // and hope the model read it the same way.
  const [options, setOptions] = useState<Options | null>(null);
  const [draft, setDraft] = useState<Spec | null>(null);
  const [tuning, setTuning] = useState(false);

  const say = useCallback((text: string, next?: "ok" | "error") => {
    setStatus(text);
    setTone(next);
  }, []);

  // The shared session (review C4/C5): the shop, its permissions and the
  // suspended notice come from one place, and a switch starts over.
  const session = useSalesSession(liffId, say);
  useEffect(() => {
    if (!session.ready) return;
    setToken(session.token);
    setLicenseId(session.licenseId);
    setAllowed(session.permissions.has("view_reports"));
    say("");
  }, [session.ready, session.token, session.licenseId, session.permissions, say]);

  // The editor'schoices must be the validator's whitelist, so it is fetched
  // rather than restated here.
  useEffect(() => {
    if (!token || !licenseId || allowed === false) return;
    let live = true;
    void (async () => {
      try {
        const response = await fetch(
          `/api/phase2/licenses/${licenseId}/reports/ai/options?language=${locale}`,
          { headers: proxyHeaders(token, licenseId) },
        );
        if (!response.ok) return;
        const data = (await response.json()) as Options;
        if (live) setOptions(data);
      } catch {
        // The editor simply stays closed; asking in words still works.
      }
    })();
    return () => {
      live = false;
    };
  }, [token, licenseId, allowed, locale]);

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
      setDraft(data.spec ?? null);
      setTuning(false);
      say(data.clarify ? copy.clarify : data.error ? data.message ?? copy.failed : copy.done, data.error ? "error" : "ok");
    } catch {
      say(copy.failed, "error");
    } finally {
      setBusy(false);
    }
  }

  /** Run an edited spec. The model is not asked again — the numbers change,
   *  the question does not. */
  async function rerun(spec: Spec) {
    if (!token || !licenseId) return;
    setBusy(true);
    say(copy.working);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/reports/ai/run`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ spec, language: locale }),
      });
      if (response.status === 422) {
        const detail = (await response.json()) as { detail?: string };
        say(detail.detail ? `${copy.specRejected} (${detail.detail})` : copy.specRejected, "error");
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      const data = (await response.json()) as Answer;
      setAnswer(data);
      setDraft(data.spec ?? spec);
      say(copy.done, "ok");
    } catch {
      say(copy.failed, "error");
    } finally {
      setBusy(false);
    }
  }

  /** Keep the draft valid for its entity: a field or grouping that belongs to
   *  the old entity would be refused by the validator, so it is dropped here
   *  instead of being sent and bounced. */
  function pickEntity(value: string) {
    const entity = options?.entities.find((e) => e.value === value);
    setDraft((current) =>
      !current || !entity
        ? current
        : {
            ...current,
            entity: value,
            filter: {},
            field: entity.numeric_fields.some((f) => f.value === current.field) ? current.field : null,
            metric: entity.numeric_fields.length === 0 ? "count" : current.metric,
            group_by: entity.group_by.some((g) => g.value === current.group_by) ? current.group_by : null,
            date_field: entity.date_fields[0]?.value ?? current.date_field,
          },
    );
  }

  const rows = answer?.result?.rows ?? [];
  const peak = Math.max(1, ...rows.map((r) => r.value));
  const entityOptions = options?.entities.find((e) => e.value === draft?.entity) ?? null;
  const filterKeys = Object.keys(draft?.filter ?? {});

  return (
    <SalesShell
      session={session}
      title={copy.title}
      liffId={liffId}
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
          {(answer.chart || (answer.files && (answer.files.csv || answer.files.html || answer.files.pdf))) && (
            <div className="card-actions">
              {answer.chart && (
                <button type="button" className="card-button" onClick={() => openExternal(answer.chart!)}>
                  {copy.openChart}
                </button>
              )}
              {answer.files?.csv && (
                <button type="button" className="card-button" onClick={() => openExternal(answer.files!.csv!)}>
                  {copy.downloadCsv}
                </button>
              )}
              {answer.files?.pdf && (
                <button type="button" className="card-button" onClick={() => openExternal(answer.files!.pdf!)}>
                  {copy.downloadPdf}
                </button>
              )}
              {answer.files?.html && (
                <button type="button" className="card-button" onClick={() => openExternal(answer.files!.html!)}>
                  {copy.openPage}
                </button>
              )}
            </div>
          )}
          {answer.quota && !answer.quota.allowed && (
            <p className="footnote">
              {copy.quotaSpent.replace("{allowance}", String(answer.quota.allowance))}
            </p>
          )}
          {answer.quota?.allowed && !answer.quota.unknown && (
            <p className="footnote">
              {copy.quotaUsed
                .replace("{used}", String(answer.quota.used))
                .replace("{allowance}", String(answer.quota.allowance))}
            </p>
          )}
          {answer.plottable === false && (
            <p className="footnote report-no-chart">
              {copy.noChart}{" "}
              {draft && options && (
                <button
                  type="button"
                  className="linklike"
                  onClick={() => setTuning(true)}
                >
                  {copy.noChartAction}
                </button>
              )}
            </p>
          )}
          {draft && options && (
            <div className="spec-editor">
              <button
                type="button"
                className="card-button spec-toggle"
                aria-expanded={tuning}
                onClick={() => setTuning((open) => !open)}
              >
                {tuning ? copy.tuneClose : copy.tune}
              </button>
              {tuning && (
                <div className="fields spec-fields">
                  <label className="field">
                    {copy.specEntity}
                    <select
                      value={draft.entity}
                      disabled={busy}
                      onChange={(event) => pickEntity(event.target.value)}
                    >
                      {options.entities.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>

                  <label className="field">
                    {copy.specMetric}
                    <select
                      value={draft.metric}
                      disabled={busy}
                      onChange={(event) => {
                        const metric = event.target.value;
                        setDraft({
                          ...draft,
                          metric,
                          // count has no field; every other metric needs one.
                          field: metric === "count" ? null : draft.field ?? entityOptions?.numeric_fields[0]?.value ?? null,
                        });
                      }}
                    >
                      {options.metrics
                        .filter((m) => m.value === "count" || (entityOptions?.numeric_fields.length ?? 0) > 0)
                        .map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                    </select>
                  </label>

                  {draft.metric !== "count" && (entityOptions?.numeric_fields.length ?? 0) > 0 && (
                    <label className="field">
                      {copy.specField}
                      <select
                        value={draft.field ?? ""}
                        disabled={busy}
                        onChange={(event) => setDraft({ ...draft, field: event.target.value })}
                      >
                        {entityOptions!.numeric_fields.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                  )}

                  <label className="field">
                    {copy.specGroupBy}
                    <select
                      value={draft.group_by ?? ""}
                      disabled={busy}
                      onChange={(event) => setDraft({ ...draft, group_by: event.target.value || null })}
                    >
                      <option value="">{copy.specNoGroup}</option>
                      {(entityOptions?.group_by ?? []).map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>

                  <label className="field">
                    {copy.specRange}
                    <select
                      value={draft.date_range ?? ""}
                      disabled={busy}
                      onChange={(event) => setDraft({ ...draft, date_range: event.target.value || null })}
                    >
                      <option value="">{copy.specAllTime}</option>
                      {options.date_ranges.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>

                  {(entityOptions?.date_fields.length ?? 0) > 1 && (
                    <label className="field">
                      {copy.specDateField}
                      <select
                        value={draft.date_field}
                        disabled={busy}
                        onChange={(event) => setDraft({ ...draft, date_field: event.target.value })}
                      >
                        {entityOptions!.date_fields.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                  )}

                  {filterKeys.length > 0 && (
                    <div className="spec-filters">
                      <span className="field-caption">{copy.specFilters}</span>
                      <div className="chat-row-chips">
                        {filterKeys.map((key) => (
                          <button
                            key={key}
                            type="button"
                            className="chip"
                            disabled={busy}
                            aria-label={`${copy.specRemoveFilter}: ${key} = ${draft.filter[key]}`}
                            onClick={() => {
                              const next = { ...draft.filter };
                              delete next[key];
                              setDraft({ ...draft, filter: next });
                            }}
                          >
                            {key} = {draft.filter[key]} ×
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="actions">
                    <button
                      type="button"
                      className="btn"
                      disabled={busy}
                      onClick={() => void rerun(draft)}
                    >
                      {busy ? copy.working : copy.rerun}
                    </button>
                    <button
                      type="button"
                      className="card-button"
                      disabled={busy || !answer.spec}
                      onClick={() => setDraft(answer.spec ?? draft)}
                    >
                      {copy.specReset}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
          <p className="footnote">{copy.footnote}</p>
        </section>
      )}
    </SalesShell>
  );
}
