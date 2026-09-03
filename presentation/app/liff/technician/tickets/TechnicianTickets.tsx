"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../../sales/_components";
import { Ticket, TicketRow } from "../../_tickets";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../../_shared";

/**
 * What a technician opens between jobs.
 *
 * Two lists, not one: "mine" is what they are committed to and "open" is
 * what they could take. A single list ordered by status buries the job
 * they are supposed to be at behind six they are not.
 *
 * The server decides what is visible (visible_to on the tickets endpoint);
 * this page never filters private tickets itself. Client-side filtering
 * would mean the data was already sent — a technician reading another
 * customer's address in a network tab is the same leak whether or not the
 * UI drew it.
 */
export default function TechnicianTickets({ liffId }: { liffId: string }) {
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
      say("");
    },
    [licenseId, memberId, say, t, token],
  );

  const initialize = useCallback(async () => {
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
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  async function claim(ticket: Ticket) {
    setBusyId(ticket.id);
    say(t.dashboard.working);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/tickets/${ticket.id}/claim`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId, "technician"),
          body: JSON.stringify({ member_id: memberId }),
        },
      );
      if (!response.ok) {
        // 409 is the normal, expected outcome of two people tapping at
        // once — it means somebody got there first, not that anything
        // broke, and it should not read like an error in the app.
        say(
          response.status === 409
            ? t.dashboard.tickets.claimFailed
            : `${t.common.error} (${response.status})`,
          "error",
        );
        return;
      }
      say(`${ticket.ticket_number} — ${t.dashboard.tickets.claimed}`, "ok");
      await load();
    } catch (error) {
      say(error instanceof Error ? error.message : t.common.error, "error");
    } finally {
      setBusyId("");
    }
  }

  async function checkIn(ticket: Ticket) {
    setBusyId(ticket.id);
    say(t.dashboard.working);
    try {
      // Real coordinates when the browser will give them, nothing when it
      // will not. Storing a location we guessed would be worse than
      // storing none: it is evidence either way.
      const position = await new Promise<GeolocationPosition | null>((resolve) => {
        if (!navigator.geolocation) return resolve(null);
        navigator.geolocation.getCurrentPosition(
          resolve, () => resolve(null), { timeout: 8000 },
        );
      });
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/tickets/${ticket.id}/check-in`,
        {
          method: "POST",
          headers: proxyHeaders(token, licenseId, "technician"),
          body: JSON.stringify({
            member_id: memberId,
            gps_lat: position?.coords.latitude ?? null,
            gps_lng: position?.coords.longitude ?? null,
          }),
        },
      );
      if (!response.ok) {
        say(`${t.common.error} (${response.status})`, "error");
        return;
      }
      say(`${ticket.ticket_number} — ${t.dashboard.tickets.checkedIn}`, "ok");
      await load();
    } finally {
      setBusyId("");
    }
  }

  const mine = tickets.filter(
    (ticket) =>
      ticket.assigned_to_ref === memberId && ticket.accept_status === "accepted",
  );
  const available = tickets.filter(
    (ticket) =>
      ticket.status !== "completed" &&
      ticket.status !== "cancelled" &&
      !(ticket.assigned_to_ref === memberId && ticket.accept_status === "accepted"),
  );
  const canClaim = permissions.has("ticket.update");

  return (
    <AppShell
      title={t.dashboard.tickets.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <div className="related-head">
        <h2>{t.dashboard.tickets.mine}</h2>
        <span className="related-count">{mine.length}</span>
      </div>
      {mine.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.tickets.emptyMine}</p>
        </div>
      ) : (
        <ul className="list">
          {mine.map((ticket) => (
            <TicketRow
              key={ticket.id}
              ticket={ticket}
              statusLabel={statusLabel(ticket.status)}
              action={
                <>
                  <span className="card-meta" style={{ flex: "1 1 100%" }}>
                    {ticket.service_address ?? "—"}
                    {ticket.customer_phone ? ` · ${ticket.customer_phone}` : ""}
                  </span>
                  {ticket.status === "assigned" && canClaim && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="primary"
                      onClick={() => void checkIn(ticket)}
                      disabled={busyId === ticket.id}
                    >
                      {busyId === ticket.id
                        ? t.dashboard.working
                        : t.dashboard.tickets.checkIn}
                    </button>
                  )}
                  {ticket.status === "in_progress" && (
                    // Closing needs a report, and a report needs typing —
                    // which chat does far better than a form squeezed onto
                    // a phone screen in someone's hallway.
                    <span className="card-meta" style={{ flex: "1 1 100%" }}>
                      {t.dashboard.tickets.closeInChat}
                    </span>
                  )}
                </>
              }
            />
          ))}
        </ul>
      )}

      <div className="related-head">
        <h2>{t.dashboard.tickets.available}</h2>
        <span className="related-count">{available.length}</span>
      </div>
      {available.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.tickets.empty}</p>
        </div>
      ) : (
        <ul className="list">
          {available.map((ticket) => (
            <TicketRow
              key={ticket.id}
              ticket={ticket}
              statusLabel={statusLabel(ticket.status)}
              action={
                canClaim && ticket.accept_status !== "accepted" ? (
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => void claim(ticket)}
                    disabled={busyId === ticket.id}
                  >
                    {busyId === ticket.id
                      ? t.dashboard.working
                      : t.dashboard.tickets.claim}
                  </button>
                ) : null
              }
            />
          ))}
        </ul>
      )}
    </AppShell>
  );
}
