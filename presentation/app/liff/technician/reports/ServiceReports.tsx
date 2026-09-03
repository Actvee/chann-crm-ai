"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../../sales/_components";
import { Audience, fetchPermissions, initLiffSession, proxyHeaders } from "../../_shared";

type ServiceReport = {
  id: string;
  report_id: string;
  ticket_id: string;
  status: string;
  report_data?: Record<string, unknown> | null;
  created_at?: string | null;
};

type Ticket = {
  id: string;
  ticket_number: string;
  customer_name?: string | null;
  service_address?: string | null;
  issue_description?: string | null;
};

/**
 * What the technician wrote, where anyone can read it.
 *
 * Check-out has required a report since Phase 13, and until now nothing
 * displayed one — so the record that justified making it mandatory
 * existed only in the database. A CS person answering "what did you
 * actually do?" had no way to answer, which is the question the report
 * was created to settle.
 *
 * One component for both audiences: a technician reads their own work, a
 * CS person reads the team's, and the server decides which rows either of
 * them gets. Two copies would let the filtering drift apart.
 */
export default function ServiceReports({
  liffId,
  audience,
}: {
  liffId: string;
  audience: Audience;
}) {
  const { t } = useLanguage();
  const [reports, setReports] = useState<ServiceReport[]>([]);
  const [tickets, setTickets] = useState<Record<string, Ticket>>({});
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [licenseId, setLicenseId] = useState("");
  const [token, setToken] = useState("");
  const [busyId, setBusyId] = useState("");
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      const headers = proxyHeaders(currentToken, license, audience);
      const response = await fetch(
        `/api/phase2/licenses/${license}/service-reports`,
        { headers },
      );
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      const rows = (await response.json()) as ServiceReport[];
      setReports(rows);

      // The ticket gives the report a customer and an address; a report
      // id alone tells a reader nothing about which visit it describes.
      const ticketsResponse = await fetch(
        `/api/phase2/licenses/${license}/tickets`, { headers },
      );
      if (!ticketsResponse.ok) {
        throw new Error(`${t.dashboard.loadFailed} (${ticketsResponse.status})`);
      }
      const all = (await ticketsResponse.json()) as Ticket[];
      setTickets(Object.fromEntries(all.map((row) => [row.id, row])));
      say("");
    },
    [audience, licenseId, say, t, token],
  );

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, audience);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      setToken(session.token);
      setLicenseId(license);
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setPermissions(await fetchPermissions(session.token, license, audience));
      await load(session.token, license);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [audience, liffId, load, say, t]);

  async function decide(report: ServiceReport, next: string) {
    setBusyId(report.id);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/service-reports/${report.id}/status`,
        {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId, audience),
          body: JSON.stringify({ status: next }),
        },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      await load();
      say(t.dashboard.saved, "ok");
    } finally {
      setBusyId("");
    }
  }

  // Only a CS person approves, and only what is still waiting. A
  // technician approving their own visit would make the step meaningless.
  const canApprove = audience === "sales" && permissions.has("ticket.update");

  // The back link must be explicit per audience: an undefined `back` falls
  // through to AppShell's default (the sales menu) and would send a
  // technician to the wrong OA's home.
  return (
    <AppShell
      title={t.dashboard.reports.title}
      back={audience === "sales" ? "/liff/sales" : "/liff/technician"}
      nav={audience === "sales"}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {reports.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.reports.empty}</p>
        </div>
      ) : (
        <ul className="list">
          {reports.map((report) => {
            const ticket = tickets[report.ticket_id];
            const data = report.report_data ?? {};
            return (
              <li
                key={report.id}
                className="card"
                data-stage={
                  report.status === "approved"
                    ? "won"
                    : report.status === "rejected"
                      ? "lost"
                      : "proposed"
                }
              >
                <div className="card-title">
                  <span className="code">{report.report_id}</span>
                  <span
                    className="badge"
                    data-stage={
                      report.status === "approved"
                        ? "won"
                        : report.status === "rejected"
                          ? "lost"
                          : "proposed"
                    }
                  >
                    {(t.dashboard.reports.status as Record<string, string>)[
                      report.status
                    ] ?? "—"}
                  </span>
                </div>

                {ticket && (
                  <div className="card-meta">
                    {ticket.ticket_number}
                    {ticket.customer_name ? ` · ${ticket.customer_name}` : ""}
                  </div>
                )}

                <dl className="fields">
                  <div className="field-row">
                    <dt>{t.dashboard.reports.foundIssue}</dt>
                    <dd>{String(data.found_issue ?? "—")}</dd>
                  </div>
                  <div className="field-row">
                    <dt>{t.dashboard.reports.workDone}</dt>
                    <dd>{String(data.work_done ?? "—")}</dd>
                  </div>
                  {data.parts_changed ? (
                    <div className="field-row">
                      <dt>{t.dashboard.reports.parts}</dt>
                      <dd>{String(data.parts_changed)}</dd>
                    </div>
                  ) : null}
                  {data.notes ? (
                    <div className="field-row">
                      <dt>{t.dashboard.fields.notes}</dt>
                      <dd>{String(data.notes)}</dd>
                    </div>
                  ) : null}
                </dl>

                {canApprove && report.status === "submitted" && (
                  <div className="card-actions">
                    <button
                      type="button"
                      className="btn"
                      data-variant="primary"
                      onClick={() => void decide(report, "approved")}
                      disabled={busyId === report.id}
                    >
                      {t.dashboard.reports.approve}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      data-variant="quiet"
                      onClick={() => void decide(report, "rejected")}
                      disabled={busyId === report.id}
                    >
                      {t.dashboard.reports.reject}
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </AppShell>
  );
}
