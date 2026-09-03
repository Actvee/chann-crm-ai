"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../_components";
import { FieldRow } from "../../_field-row";
import { fetchPermissions, initLiffSession, openExternal, proxyHeaders } from "../../_shared";

type Step = {
  id: string;
  step_order: number;
  approver_type: string;
  approver_ref: string;
  status: string;
};
type Report = {
  id: string;
  report_id: string;
  ticket_id: string;
  status: string;
  report_data?: Record<string, unknown> | null;
};
type Ticket = {
  id: string;
  ticket_number: string;
  customer_name?: string | null;
  service_address?: string | null;
  issue_description?: string | null;
};
type Pending = { step: Step; report: Report | null; ticket: Ticket | null };
type ActResult = { report_status?: string; survey_sent?: boolean; document_url?: string | null };

/**
 * The approval queue — Phase 14-C, the dashboard side of "อนุมัติ SR-…".
 *
 * Every button here calls the same Application route that chat's handler
 * calls into (services/approval.py), so approving from a phone and from
 * this page are one transaction with one set of notifications. What this
 * page adds is the thing chat cannot show: the whole report next to the
 * ticket it belongs to, before deciding.
 */
export default function ApprovalQueue({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const [rows, setRows] = useState<Pending[]>([]);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [licenseId, setLicenseId] = useState("");
  const [token, setToken] = useState("");
  const [busyId, setBusyId] = useState("");
  const [rejecting, setRejecting] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      const response = await fetch(
        `/api/phase2/licenses/${license}/approvals/pending`,
        { headers: proxyHeaders(currentToken, license, "sales") },
      );
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      setRows((await response.json()) as Pending[]);
      say("");
    },
    [licenseId, say, t, token],
  );

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, "sales");
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      setToken(session.token);
      setLicenseId(license);
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setPermissions(await fetchPermissions(session.token, license, "sales"));
      await load(session.token, license);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  async function act(row: Pending, approve: boolean) {
    const code = row.report?.report_id ?? "";
    // Two literal URLs, not one with the verb interpolated: check-parity
    // reads the path segments out of the source to prove the dashboard
    // can approve AND reject, and an expression there is invisible to it.
    const url = approve
      ? `/api/phase2/licenses/${licenseId}/approvals/${row.step.id}/approve`
      : `/api/phase2/licenses/${licenseId}/approvals/${row.step.id}/reject`;
    setBusyId(row.step.id);
    try {
      const response = await fetch(
        url,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "sales"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify(approve ? {} : { reason: reason.trim() }),
        },
      );
      if (!response.ok) {
        say(
          response.status === 409 || response.status === 404
            ? t.dashboard.approvals.conflict
            : response.status === 403
              ? t.dashboard.noPermission
              : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      const result = (await response.json()) as ActResult;
      const message = !approve
        ? t.dashboard.approvals.rejected
        : result.report_status === "approved"
          ? result.survey_sent
            ? t.dashboard.approvals.approvedSurvey
            : t.dashboard.approvals.approvedNoLine
          : t.dashboard.approvals.approvedNext;
      setRejecting("");
      setReason("");
      await load();
      say(
        message.replace("{code}", code)
          + (result.document_url ? ` · ${t.dashboard.approvals.pdfReady}` : ""),
        "ok",
      );
      if (result.document_url) openExternal(result.document_url);
    } catch {
      say(t.dashboard.related.actionFailed, "error");
    } finally {
      setBusyId("");
    }
  }

  const canApprove = permissions.has("approval.approve");
  const canReject = permissions.has("approval.reject");
  const canManage = permissions.has("approval.manage");

  return (
    <AppShell
      title={t.dashboard.approvals.title}
      back="/liff/sales"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <p className="page-intro">{t.dashboard.approvals.intro}</p>
      {canManage && (
        <div className="actions" style={{ marginBottom: 16 }}>
          <Link className="btn" data-variant="quiet" href="/liff/sales/approvals/settings">
            {t.dashboard.approvals.settingsLink}
          </Link>
        </div>
      )}

      {rows.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.approvals.empty}</p>
        </div>
      ) : (
        <ul className="list">
          {rows.map((row) => {
            const data = row.report?.report_data ?? {};
            const code = row.report?.report_id ?? "";
            const busy = busyId === row.step.id;
            return (
              <li key={row.step.id} className="card" data-stage="proposed">
                <div className="card-title">
                  <span className="code">{code}</span>
                  <span className="badge" data-stage="proposed">
                    {t.dashboard.approvals.stepOf.replace("{n}", String(row.step.step_order))}
                  </span>
                </div>
                {row.ticket && (
                  <div className="card-meta">
                    {row.ticket.ticket_number}
                    {row.ticket.customer_name ? ` · ${row.ticket.customer_name}` : ""}
                    {row.ticket.issue_description ? ` · ${row.ticket.issue_description}` : ""}
                  </div>
                )}

                <dl className="fields">
                  <FieldRow label={t.dashboard.reports.foundIssue}>
                    {String(data.found_issue ?? "—")}
                  </FieldRow>
                  <FieldRow label={t.dashboard.reports.workDone}>
                    {String(data.work_done ?? "—")}
                  </FieldRow>
                  {data.parts_changed ? (
                    <FieldRow label={t.dashboard.reports.parts}>
                      {String(data.parts_changed)}
                    </FieldRow>
                  ) : null}
                  {rejecting === row.step.id && (
                    <FieldRow label={t.dashboard.approvals.reasonLabel}>
                      {(id) => (
                        <>
                          <textarea
                            id={id}
                            rows={2}
                            autoFocus
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            aria-describedby={`${id}-hint`}
                          />
                          <span id={`${id}-hint`} className="hint">
                            {t.dashboard.approvals.reasonHint}
                          </span>
                        </>
                      )}
                    </FieldRow>
                  )}
                </dl>

                <div className="card-actions">
                  {rejecting === row.step.id ? (
                    <>
                      <button
                        type="button"
                        className="btn"
                        data-variant="primary"
                        disabled={busy || !reason.trim()}
                        onClick={() => void act(row, false)}
                      >
                        {busy ? t.dashboard.saving : t.dashboard.approvals.confirmReject}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        disabled={busy}
                        onClick={() => {
                          setRejecting("");
                          setReason("");
                        }}
                      >
                        {t.common.cancel}
                      </button>
                    </>
                  ) : (
                    <>
                      {canApprove && (
                        <button
                          type="button"
                          className="btn"
                          data-variant="primary"
                          disabled={busyId !== ""}
                          onClick={() => void act(row, true)}
                        >
                          {busy ? t.dashboard.saving : t.dashboard.approvals.approve}
                        </button>
                      )}
                      {canReject && (
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          disabled={busyId !== ""}
                          onClick={() => {
                            setRejecting(row.step.id);
                            setReason("");
                          }}
                        >
                          {t.dashboard.approvals.reject}
                        </button>
                      )}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </AppShell>
  );
}
