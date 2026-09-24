"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { useSalesSession } from "../../_session";
import { SalesShell } from "../../_shell";
import { openExternal, proxyHeaders } from "../../_lib";
import { useFormatters } from "../../_format";
import { PlanReason, planHas } from "../../../_plan";

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
  quota?: {
    allowed: boolean;
    used: number;
    allowance: number;
    unknown: boolean;
    /** What the one credit paid for (final review I2): a words-only answer
     *  is never labelled as a chart. */
    charged_for?: "picture" | "question";
  };
  /** Final review I3: the question was one of the five, answered free by
   *  that report — the same sentence is free on LINE too. */
  basic?: BasicReport;
  free?: boolean;
};

/** Round 21C — the five basic reports (`/reports/basic/{key}`, Task 12).
 *  Credit-free and computed by code: every number here is server work, not
 *  browser arithmetic. */
type BasicRow = { key: string; label_th: string; label_en: string; value: number; count?: number };
type BasicReport = {
  key: string;
  title_th: string;
  title_en: string;
  unit: "money" | "count" | "score";
  headline: { label_th: string; label_en: string; value: number };
  rows: BasicRow[];
  notes_th: string[];
  notes_en: string[];
};

const BASIC_KEYS = [
  "pipeline_value", "won_this_month", "open_jobs_by_tech",
  "outstanding_invoices", "satisfaction_avg",
] as const;

/** Round 21D (owner decision Q4): the two basic reports that are service
 *  work — entitlements.SERVICE_BASIC_REPORTS. A plan without service shows
 *  the other three; the API refuses these two there anyway. */
const SERVICE_KEYS: readonly string[] = ["open_jobs_by_tech", "satisfaction_avg"];

/** Phase 17 — the report viewer. One question box, the model turns it
 *  into a whitelisted spec, the numbers come back as a table with bars. */
export default function AiReports({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const copy = t.dashboard.aiReports;
  // Fix round 1: reuse the file's own money formatter (locale mapped to
  // "th-TH"/"en-US" by `intlLocale`, the fix for review C11's Buddhist-year
  // bug) instead of a second `Intl.NumberFormat` on the raw two-letter tag.
  const { money } = useFormatters();
  const formatBasicValue = (value: number, unit: BasicReport["unit"]) =>
    money(value, unit === "count" ? 0 : 2);
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
  // Round 21C — the five basic report cards. `null` = still loading (or not
  // yet fetched); `basicFailed[key]` names a card whose fetch failed so it
  // shows an error, not an eternal skeleton and never a 0.
  const [basic, setBasic] = useState<Record<string, BasicReport | null>>({});
  const [basicFailed, setBasicFailed] = useState<Record<string, boolean>>({});
  // Fix round 1 — "rows on demand": the top 3 rows show at rest; a report
  // with more opens the rest in place instead of hiding them for good.
  const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});

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

  // Round 21D (spec §5.6, §8.5): the question box is the AI credit road —
  // on a plan without it the box says so instead of spending; the basic
  // reports stay free on every plan. `serviceOn` hides Q4's two cards.
  const aiOn = planHas(session.plan, "quota.ai_reports_per_month");
  const serviceOn = planHas(session.plan, "feature.service");
  const shownKeys = BASIC_KEYS.filter((key) => !SERVICE_KEYS.includes(key) || serviceOn);

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

  // Round 21C — five reports the owner recognises, computed by code, never
  // spending an AI credit. `null` still means "loading"; a failed key is
  // recorded separately so its card can say so instead of loading forever.
  //
  // Fix round 1: the five requests are independent of each other, so they
  // are fired together (`Promise.allSettled`, not a `for`-await loop) —
  // a slow first card must not hold the other four in their skeleton
  // state on the LINE in-app browser's mobile network. Each key still has
  // its own try/catch, so one failure still leaves the other four alone.
  useEffect(() => {
    if (!token || !licenseId || allowed === false) return;
    let live = true;
    const load = async (key: (typeof BASIC_KEYS)[number]) => {
      if (SERVICE_KEYS.includes(key) && !serviceOn) return;
      try {
        const response = await fetch(
          `/api/phase2/licenses/${licenseId}/reports/basic/${key}`,
          { headers: proxyHeaders(token, licenseId) },
        );
        if (!response.ok) {
          if (live) setBasicFailed((current) => ({ ...current, [key]: true }));
          return;
        }
        const data = (await response.json()) as BasicReport;
        if (live) {
          setBasic((current) => ({ ...current, [key]: data }));
          setBasicFailed((current) => ({ ...current, [key]: false }));
        }
      } catch {
        // One card that cannot load leaves the other four alone.
        if (live) setBasicFailed((current) => ({ ...current, [key]: true }));
      }
    };
    void Promise.allSettled(BASIC_KEYS.map((key) => load(key)));
    return () => {
      live = false;
    };
  }, [token, licenseId, allowed, serviceOn]);

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
        <section className="basic-reports">
          <h2>{copy.basic.heading}</h2>
          <p className="basic-intro">{copy.basic.intro}</p>
          <div className="basic-grid">
            {shownKeys.map((key) => {
              const report = basic[key];
              const failed = basicFailed[key];
              const words = copy.basic[key];
              const expanded = !!expandedRows[key];
              const rows = report ? (expanded ? report.rows : report.rows.slice(0, 3)) : [];
              const hasMore = (report?.rows.length ?? 0) > 3;
              return (
                <article key={key} className="basic-card" aria-busy={!report && !failed}>
                  <h3>{words.title}</h3>
                  <p className="basic-blurb">{words.blurb}</p>
                  {report ? (
                    <>
                      <p className="basic-value">
                        {formatBasicValue(report.headline.value, report.unit)}
                      </p>
                      <ul className="basic-rows">
                        {rows.map((row) => (
                          <li key={row.key}>
                            <span>{locale === "en" ? row.label_en : row.label_th}</span>
                            <b>{formatBasicValue(row.value, report.unit)}</b>
                          </li>
                        ))}
                      </ul>
                      {hasMore ? (
                        <button
                          type="button"
                          className="basic-toggle"
                          aria-expanded={expanded}
                          onClick={() =>
                            setExpandedRows((current) => ({ ...current, [key]: !current[key] }))
                          }
                        >
                          {expanded
                            ? copy.basic.showLess
                            : copy.basic.showAll.replace("{count}", String(report.rows.length))}
                        </button>
                      ) : null}
                      {(locale === "en" ? report.notes_en : report.notes_th)[0] ? (
                        <p className="basic-note">
                          {(locale === "en" ? report.notes_en : report.notes_th)[0]}
                        </p>
                      ) : null}
                    </>
                  ) : failed ? (
                    <p className="basic-error" role="alert">
                      {copy.basic.loadError.replace("{title}", words.title)}
                    </p>
                  ) : (
                    <p className="basic-value skeleton" aria-hidden="true">&nbsp;</p>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      )}

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
              disabled={busy || !token || !aiOn}
              aria-describedby={!aiOn ? "ai-locked-reason" : undefined}
            />
          </label>
          {/* The reason, as text under the box it disables (ui-ux-pro-max
              disabled-states) — never only a tooltip. */}
          {!aiOn && (
            <div id="ai-locked-reason">
              <PlanReason>{t.dashboard.plan.aiLocked}</PlanReason>
            </div>
          )}
          <div className="actions">
            <button type="submit" className="btn" disabled={busy || !token || !question.trim() || !aiOn}>
              {busy ? copy.working : copy.ask}
            </button>
          </div>
          {/* Examples would spend a credit the plan does not have. */}
          {aiOn && (
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
          )}
        </form>
      )}

      {answer?.clarify && (
        <section className="card">
          <h2 className="card-title">{copy.clarify}</h2>
          <p>{answer.clarify}</p>
        </section>
      )}

      {/* One of the five, asked in the question box: the report itself,
          free, and it says so (final review I3 — the same sentence is
          free on LINE). A plain footnote, not a toast: it is part of the
          answer, not a passing event (ui-ux-pro-max: success-feedback). */}
      {answer?.basic && (
        <section className="card report-card" aria-live="polite">
          <h2 className="card-title">
            {locale === "en" ? answer.basic.title_en : answer.basic.title_th}
          </h2>
          <p className="report-big">
            {formatBasicValue(answer.basic.headline.value, answer.basic.unit)}
          </p>
          <ul className="basic-rows">
            {answer.basic.rows.map((row) => (
              <li key={row.key}>
                <span>{locale === "en" ? row.label_en : row.label_th}</span>
                <b>{formatBasicValue(row.value, answer.basic!.unit)}</b>
              </li>
            ))}
          </ul>
          {(locale === "en" ? answer.basic.notes_en : answer.basic.notes_th)[0] ? (
            <p className="basic-note">
              {(locale === "en" ? answer.basic.notes_en : answer.basic.notes_th)[0]}
            </p>
          ) : null}
          <p className="footnote">{copy.basic.answeredFree}</p>
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
          {/* The credit, said as what it paid for: a picture, or a
              words-only answer that has no chart to call "drawn by AI"
              (final review I2). */}
          {answer.quota && !answer.quota.allowed && (
            <p className="footnote">
              {(answer.quota.charged_for === "picture" ? copy.quotaSpent : copy.quotaSpentWords)
                .replace("{allowance}", String(answer.quota.allowance))}
            </p>
          )}
          {answer.quota?.allowed && !answer.quota.unknown && (
            <p className="footnote">
              {(answer.quota.charged_for === "picture" ? copy.quotaUsed : copy.quotaUsedWords)
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
