"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../sales/_components";
import { FieldRow } from "../_field-row";
import { Ticket, TicketRow } from "../_tickets";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../_shared";

/**
 * The technician's home — their own, not the sales dashboard reskinned.
 *
 * Owner requirement (2 Sep): a technician opens LINE between jobs and
 * needs exactly four things — what am I committed to, what could I take,
 * start a visit, finish a visit with the report. Everything else on the
 * sales dashboard (pipeline, quotes, customer lists) is noise on a phone
 * held in one hand in a stairwell, so none of it is here.
 *
 * Finishing a visit and filing the service report are ONE action
 * (check-out carries report_data), because the Data Tier writes them in
 * one transaction — a screen that let you close without reporting would
 * un-make that guarantee one tap at a time.
 */
export default function TechnicianHome({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const statusLabel = (status: string) =>
    (t.dashboard.tickets.status as Record<string, string>)[status] ?? status;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [memberId, setMemberId] = useState("");
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");
  const [reportFor, setReportFor] = useState<Ticket | null>(null);
  // The three things a service report says (Data Tier REPORT_REQUIRED:
  // found_issue + work_done are the gate; parts are optional). The first
  // version of this form posted one "work_summary" box, which the gate
  // refused every single time — the report's shape is the API's, not ours.
  const [reportFound, setReportFound] = useState("");
  const [reportDone, setReportDone] = useState("");
  const [reportParts, setReportParts] = useState("");
  const reportComplete = reportFound.trim() !== "" && reportDone.trim() !== "";

  function resetReport() {
    setReportFound("");
    setReportDone("");
    setReportParts("");
  }

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId, member = memberId) => {
      if (!currentToken || !license) return;
      const response = await fetch(
        `/api/phase2/licenses/${license}/tickets${member ? `?visible_to=${member}` : ""}`,
        { headers: proxyHeaders(currentToken, license, "technician") },
      );
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      setTickets((await response.json()) as Ticket[]);
    },
    [token, licenseId, memberId, t],
  );

  const onReady = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, "technician");
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      setToken(session.token);
      setLicenseId(license);
      if (!session.memberships.length) {
        say(t.liff.noCompany, "error");
        return;
      }
      const member = session.memberships[0]?.member_id ?? "";
      setMemberId(member);
      setPermissions(await fetchPermissions(session.token, license, "technician"));
      await load(session.token, license, member);
      say("", undefined);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  async function claim(ticket: Ticket) {
    setBusyId(ticket.id);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/tickets/${ticket.id}/claim`,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "technician"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ member_id: memberId }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      say(`${ticket.ticket_number} — ${t.dashboard.tickets.claimed}`, "ok");
      await load();
    } catch {
      say(t.dashboard.tickets.claimFailed, "error");
    } finally {
      setBusyId("");
    }
  }

  async function checkIn(ticket: Ticket) {
    setBusyId(ticket.id);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/tickets/${ticket.id}/check-in`,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "technician"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ member_id: memberId }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      say(`${ticket.ticket_number} — ${t.dashboard.technician.checkedIn}`, "ok");
      await load();
    } catch {
      say(t.dashboard.technician.actionFailed, "error");
    } finally {
      setBusyId("");
    }
  }

  /** Check-out IS the service report — one call, one transaction. */
  async function checkOut() {
    if (!reportFor) return;
    setBusyId(reportFor.id);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/tickets/${reportFor.id}/check-out`,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "technician"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            member_id: memberId,
            report_data: {
              found_issue: reportFound.trim(),
              work_done: reportDone.trim(),
              ...(reportParts.trim() ? { parts_changed: reportParts.trim() } : {}),
            },
          }),
        },
      );
      if (!response.ok) {
        // The gate names what is missing (Thai labels from the Data
        // Tier); saying so beats a generic failure the technician
        // cannot act on.
        const body = (await response.json().catch(() => null)) as
          | { detail?: { missing?: string[] } | string }
          | null;
        const missing =
          body && typeof body.detail === "object" && body.detail?.missing?.length
            ? body.detail.missing
            : null;
        say(
          missing
            ? t.dashboard.technician.checkoutBlocked.replace("{missing}", missing.join(", "))
            : t.dashboard.technician.actionFailed,
          "error",
        );
        return;
      }
      say(
        `${reportFor.ticket_number} — ${t.dashboard.technician.checkedOut}`,
        "ok",
      );
      setReportFor(null);
      resetReport();
      await load();
    } catch {
      say(t.dashboard.technician.actionFailed, "error");
    } finally {
      setBusyId("");
    }
  }

  const mine = tickets.filter(
    (x) => x.assigned_to_ref === memberId && x.accept_status === "accepted",
  );
  // Ticket statuses are open/assigned/in_progress/completed/cancelled;
  // "closed" was never one, so the old filter kept finished jobs in the
  // open list forever.
  const open = tickets.filter(
    (x) =>
      x.status !== "completed" &&
      x.status !== "cancelled" &&
      !(x.assigned_to_ref === memberId && x.accept_status === "accepted"),
  );
  const canWork = permissions.has("ticket.update");

  return (
    <div data-theme="technician">
      <AppShell
        title={t.dashboard.technician.home}
        back={null}
        liffId={liffId}
        onReady={onReady}
        onSdkError={() => say(t.dashboard.openFailed, "error")}
        status={status}
        statusTone={tone}
      >
        <section className="section">
          <div className="section-head">
            <h2>
              {t.dashboard.technician.myJobs} ({mine.length})
            </h2>
            <a className="btn" data-variant="quiet" href="/liff/technician/reports">
              {t.dashboard.technician.myReports}
            </a>
          </div>
          {mine.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.technician.noJobs}</p>
            </div>
          ) : (
            <ul className="list">
              {mine.map((ticket) => (
                <li key={ticket.id} className="card">
                  <TicketRow
                    ticket={ticket}
                    statusLabel={statusLabel(ticket.status)}
                  />
                  {canWork && (
                    <div className="card-actions">
                      {ticket.status !== "in_progress" && (
                        <button
                          type="button"
                          className="btn"
                          data-variant="primary"
                          disabled={busyId !== ""}
                          onClick={() => void checkIn(ticket)}
                        >
                          {busyId === ticket.id
                            ? t.dashboard.related.saving
                            : t.dashboard.technician.checkIn}
                        </button>
                      )}
                      {ticket.status === "in_progress" && (
                        <button
                          type="button"
                          className="btn"
                          data-variant="primary"
                          disabled={busyId !== ""}
                          onClick={() => {
                            setReportFor(ticket);
                            resetReport();
                          }}
                        >
                          {t.dashboard.technician.checkOut}
                        </button>
                      )}
                    </div>
                  )}
                  {reportFor?.id === ticket.id && (
                    <dl className="fields">
                      <FieldRow label={t.dashboard.reports.foundIssue}>
                        {(id) => (
                          // The form appears because they just tapped
                          // "ปิดงาน"; putting the caret in the first box
                          // is the next thing they would do anyway.
                          <textarea
                            id={id}
                            rows={2}
                            autoFocus
                            value={reportFound}
                            onChange={(e) => setReportFound(e.target.value)}
                          />
                        )}
                      </FieldRow>
                      <FieldRow label={t.dashboard.reports.workDone}>
                        {(id) => (
                          <>
                            <textarea
                              id={id}
                              rows={2}
                              value={reportDone}
                              onChange={(e) => setReportDone(e.target.value)}
                              aria-describedby={`${id}-hint`}
                            />
                            {/* Says why the submit button is not yet
                                live, rather than leaving a dead button
                                to be explained by trial. */}
                            <span id={`${id}-hint`} className="hint">
                              {t.dashboard.technician.reportHint}
                            </span>
                          </>
                        )}
                      </FieldRow>
                      <FieldRow label={t.dashboard.technician.partsOptional}>
                        {(id) => (
                          <input
                            id={id}
                            value={reportParts}
                            onChange={(e) => setReportParts(e.target.value)}
                          />
                        )}
                      </FieldRow>
                      <div className="actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          disabled={busyId !== ""}
                          onClick={() => setReportFor(null)}
                        >
                          {t.dashboard.related.cancelForm}
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-variant="primary"
                          disabled={busyId !== "" || !reportComplete}
                          onClick={() => void checkOut()}
                        >
                          {busyId === ticket.id
                            ? t.dashboard.related.saving
                            : t.dashboard.technician.submitReport}
                        </button>
                      </div>
                    </dl>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="section">
          <div className="section-head">
            <h2>
              {t.dashboard.technician.openJobs} ({open.length})
            </h2>
          </div>
          {open.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.technician.noOpenJobs}</p>
            </div>
          ) : (
            <ul className="list">
              {open.map((ticket) => (
                <li key={ticket.id} className="card">
                  <TicketRow
                    ticket={ticket}
                    statusLabel={statusLabel(ticket.status)}
                  />
                  {canWork && (
                    <div className="card-actions">
                      <button
                        type="button"
                        className="btn"
                        data-variant="primary"
                        disabled={busyId !== ""}
                        onClick={() => void claim(ticket)}
                      >
                        {busyId === ticket.id
                          ? t.dashboard.related.saving
                          : t.dashboard.tickets.claim}
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </AppShell>
    </div>
  );
}
