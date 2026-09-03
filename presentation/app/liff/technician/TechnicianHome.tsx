"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../sales/_components";
import { FieldRow } from "../_field-row";
import { ProfileCard } from "../_profile-card";
import { ShopSwitcher } from "../_shop-switcher";
import { Ticket, TicketRow } from "../_tickets";
import { Membership, fetchPermissions, initLiffSession, proxyHeaders } from "../_shared";

type ServiceReport = {
  id: string;
  ticket_id: string;
  status: string;
  created_at?: string | null;
};

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
  const [reports, setReports] = useState<ServiceReport[]>([]);
  const [shopName, setShopName] = useState("");
  const [shops, setShops] = useState<Membership[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");
  const [reportFor, setReportFor] = useState<Ticket | null>(null);
  const [declineFor, setDeclineFor] = useState<Ticket | null>(null);
  const [declineReason, setDeclineReason] = useState("");
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
      const headers = proxyHeaders(currentToken, license, "technician");
      const [response, reportsRes] = await Promise.all([
        fetch(
          `/api/phase2/licenses/${license}/tickets${member ? `?visible_to=${member}` : ""}`,
          { headers },
        ),
        // The server scopes the list to this technician's own reports.
        fetch(`/api/phase2/licenses/${license}/service-reports`, { headers }),
      ]);
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      setTickets((await response.json()) as Ticket[]);
      // Reports are the secondary fact on this page; a failure there
      // shows as an empty list rather than blocking the jobs.
      setReports(reportsRes.ok ? ((await reportsRes.json()) as ServiceReport[]) : []);
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
      setShopName(session.memberships[0]?.company_name ?? "");
      setShops(session.memberships);
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
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        const detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? "");
        say(
          detail.includes("team lead accepts")
            ? t.dashboard.technician.leadFirst
            : t.dashboard.tickets.claimFailed,
          "error",
        );
        return;
      }
      const row = (await response.json()) as Ticket;
      say(
        `${ticket.ticket_number} — ${
          row.assigned_target_type === "technician_team"
            ? t.dashboard.technician.acceptedForTeam
            : t.dashboard.tickets.claimed
        }`,
        "ok",
      );
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
      if (!response.ok) {
        // The Data tier says why (its wording is stable); the person gets
        // the reason in their language, not "try again".
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        const detail = typeof body?.detail === "string" ? body.detail : "";
        const reason = detail.includes("already")
          ? t.dashboard.technician.alreadyCheckedIn
          : detail.includes("not assigned")
            ? t.dashboard.technician.notYours
            : detail.includes("cannot be checked")
              ? t.dashboard.technician.jobClosed
              : t.dashboard.technician.actionFailed;
        say(reason, "error");
        await load();
        return;
      }
      say(`${ticket.ticket_number} — ${t.dashboard.technician.checkedIn}`, "ok");
      await load();
    } catch {
      say(t.dashboard.technician.actionFailed, "error");
    } finally {
      setBusyId("");
    }
  }

  /** 13.1: a picture from the phone's camera goes on the job (the twin
   *  of sending it in chat). Read as a data: URL — no multipart. */
  async function addPhoto(ticket: Ticket, file: File | null) {
    if (!file) return;
    setBusyId(ticket.id);
    try {
      const image = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/tickets/${ticket.id}/photos`,
        {
          method: "POST",
          headers: { ...proxyHeaders(token, licenseId, "technician"), "Content-Type": "application/json" },
          body: JSON.stringify({
            image,
            photo_type: ticket.status === "in_progress" ? "evidence" : "checkin",
          }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      say(`${ticket.ticket_number} — ${t.dashboard.technician.photoAdded}`, "ok");
    } catch {
      say(t.dashboard.technician.photoFailed, "error");
    } finally {
      setBusyId("");
    }
  }

  /** 12.4: say no; the job returns to CS, nobody is auto-assigned. */
  async function decline() {
    if (!declineFor) return;
    setBusyId(declineFor.id);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/tickets/${declineFor.id}/reject`,
        {
          method: "POST",
          headers: {
            ...proxyHeaders(token, licenseId, "technician"),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ member_id: memberId, reason: declineReason.trim() }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      say(`${declineFor.ticket_number} — ${t.dashboard.technician.declined}`, "ok");
      setDeclineFor(null);
      setDeclineReason("");
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

  async function switchShop(licenseIdNext: string) {
    const next = shops.find((s) => s.license_id === licenseIdNext);
    if (!next) return;
    const member = next.member_id ?? "";
    setLicenseId(next.license_id);
    setShopName(next.company_name);
    setMemberId(member);
    try {
      setPermissions(await fetchPermissions(token, next.license_id, "technician"));
      await load(token, next.license_id, member);
      say(t.dashboard.customer.shopSwitched, "ok");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }

  const mine = tickets.filter(
    (x) => x.assigned_to_ref === memberId && x.accept_status === "accepted",
  );
  // Given to me by CS and not yet answered: accept (claim) or decline.
  const offered = tickets.filter(
    (x) =>
      x.assigned_to_ref === memberId &&
      x.accept_status !== "accepted" &&
      x.status !== "completed" &&
      x.status !== "cancelled",
  );
  // Given to a team I am on, not yet accepted by its lead (12.4).
  const offeredToTeam = tickets.filter(
    (x) =>
      x.assigned_target_type === "technician_team" &&
      x.accept_status !== "accepted" &&
      x.status !== "completed" &&
      x.status !== "cancelled",
  );
  // Accepted for the team, waiting for one of us to take it.
  const teamOpen = tickets.filter(
    (x) =>
      x.assigned_target_type === "technician_team" &&
      x.accept_status === "accepted" &&
      x.status !== "completed" &&
      x.status !== "cancelled",
  );
  // Open = nobody has taken it. It used to exclude only MY accepted
  // jobs, so a colleague's job stayed in this list for everyone — and
  // the one I had just taken looked like it was still open (owner, 3
  // Sep). Ticket statuses are open/assigned/in_progress/completed/
  // cancelled; "closed" was never one.
  const open = tickets.filter(
    (x) =>
      x.status !== "completed" &&
      x.status !== "cancelled" &&
      x.accept_status !== "accepted" &&
      x.assigned_to_ref !== memberId &&
      x.assigned_target_type !== "technician_team",
  );
  const canWork = permissions.has("ticket.update");
  const reportStatus = (code: string) =>
    (t.dashboard.reports.status as Record<string, string>)[code] ?? code;
  const ticketNumber = (ticketId: string) =>
    tickets.find((x) => x.id === ticketId)?.ticket_number ?? "";
  // Newest three: passed, sent back, or still with CS — the answer to
  // "did my last report go through?" without opening the full list.
  const recentReports = [...reports]
    .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
    .slice(0, 3);

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
        {shops.length > 1 && (
          <ShopSwitcher
            token={token}
            audience="technician"
            shops={shops}
            current={licenseId}
            label={t.dashboard.customer.shopSwitch}
            onSwitched={(id) => void switchShop(id)}
          />
        )}

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
                    statusLabel={
                      ticket.status === "in_progress"
                        ? statusLabel(ticket.status)
                        : t.dashboard.technician.waitingCheckIn
                    }
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
                      <label className="btn" data-variant="quiet">
                        {t.dashboard.technician.addPhoto}
                        <input
                          type="file"
                          accept="image/*"
                          capture="environment"
                          hidden
                          disabled={busyId !== ""}
                          onChange={(e) => {
                            void addPhoto(ticket, e.target.files?.[0] ?? null);
                            e.target.value = "";
                          }}
                        />
                      </label>
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

        {offeredToTeam.length > 0 && (
          <section className="section callout" data-tone="ok">
            <div className="section-head">
              <h2>
                {t.dashboard.technician.offeredToTeam} ({offeredToTeam.length})
              </h2>
            </div>
            <ul className="list">
              {offeredToTeam.map((ticket) => (
                <li key={ticket.id} className="card">
                  <TicketRow ticket={ticket} statusLabel={statusLabel(ticket.status)} />
                  {canWork && (
                    <div className="card-actions">
                      <button
                        type="button"
                        className="btn"
                        data-variant="primary"
                        disabled={busyId !== ""}
                        onClick={() => void claim(ticket)}
                      >
                        {busyId === ticket.id ? t.dashboard.related.saving : t.dashboard.technician.acceptForTeam}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        disabled={busyId !== ""}
                        onClick={() => {
                          setDeclineFor(ticket);
                          setDeclineReason("");
                        }}
                      >
                        {t.dashboard.technician.decline}
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {teamOpen.length > 0 && (
          <section className="section">
            <div className="section-head">
              <h2>
                {t.dashboard.technician.teamOpen} ({teamOpen.length})
              </h2>
            </div>
            <ul className="list">
              {teamOpen.map((ticket) => (
                <li key={ticket.id} className="card">
                  <TicketRow ticket={ticket} statusLabel={statusLabel(ticket.status)} />
                  {canWork && (
                    <div className="card-actions">
                      <button
                        type="button"
                        className="btn"
                        data-variant="primary"
                        disabled={busyId !== ""}
                        onClick={() => void claim(ticket)}
                      >
                        {busyId === ticket.id ? t.dashboard.related.saving : t.dashboard.tickets.claim}
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {offered.length > 0 && (
          <section className="section callout" data-tone="ok">
            <div className="section-head">
              <h2>
                {t.dashboard.technician.offeredToYou} ({offered.length})
              </h2>
            </div>
            <ul className="list">
              {offered.map((ticket) => (
                <li key={ticket.id} className="card">
                  <TicketRow ticket={ticket} statusLabel={statusLabel(ticket.status)} />
                  {canWork && (
                    <div className="card-actions">
                      <button
                        type="button"
                        className="btn"
                        data-variant="primary"
                        disabled={busyId !== ""}
                        onClick={() => void claim(ticket)}
                      >
                        {busyId === ticket.id ? t.dashboard.related.saving : t.dashboard.tickets.claim}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        disabled={busyId !== ""}
                        onClick={() => {
                          setDeclineFor(ticket);
                          setDeclineReason("");
                        }}
                      >
                        {t.dashboard.technician.decline}
                      </button>
                    </div>
                  )}
                  {declineFor?.id === ticket.id && (
                    <dl className="fields">
                      <FieldRow label={t.dashboard.technician.declineReason}>
                        {(id) => (
                          <input
                            id={id}
                            autoFocus
                            value={declineReason}
                            onChange={(e) => setDeclineReason(e.target.value)}
                          />
                        )}
                      </FieldRow>
                      <div className="actions">
                        <button
                          type="button"
                          className="btn"
                          data-variant="quiet"
                          disabled={busyId !== ""}
                          onClick={() => setDeclineFor(null)}
                        >
                          {t.dashboard.related.cancelForm}
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-variant="danger"
                          disabled={busyId !== ""}
                          onClick={() => void decline()}
                        >
                          {t.dashboard.technician.decline}
                        </button>
                      </div>
                    </dl>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

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

        <section className="section">
          <div className="section-head">
            <h2>{t.dashboard.technician.recentReports}</h2>
            <a className="btn" data-variant="quiet" href="/liff/technician/reports">
              {t.dashboard.technician.allReports}
            </a>
          </div>
          {recentReports.length === 0 ? (
            <div className="empty">
              <p>{t.dashboard.technician.noReports}</p>
            </div>
          ) : (
            <ul className="list">
              {recentReports.map((report) => (
                <li key={report.id} className="card">
                  <div className="card-title">
                    {ticketNumber(report.ticket_id) || report.id.slice(0, 8)}
                    <span
                      className="badge"
                      data-tone={
                        report.status === "approved"
                          ? "ok"
                          : report.status === "rejected"
                            ? "danger"
                            : undefined
                      }
                      style={{ marginLeft: 8 }}
                    >
                      {reportStatus(report.status)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {token && (
          <ProfileCard token={token} audience="technician" shopName={shopName} />
        )}
      </AppShell>
    </div>
  );
}
