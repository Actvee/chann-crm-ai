"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../_components";
import { FieldRow } from "../../_field-row";
import { Ticket, formatWhen, ticketStage } from "../../_tickets";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

type Technician = { id: string; display_name: string; phone?: string | null };
type Team = { id: string; team_name: string };

type Draft = {
  customer_name: string;
  customer_phone: string;
  service_address: string;
  serial_number: string;
  scheduled_date: string;
  scheduled_time: string;
};

/**
 * The dispatcher's view: which tickets are waiting, what is stopping
 * each one, and — since 3 Sep — the three things chat could already do
 * and this page could not: dispatch (to a technician or a team, through
 * the same gate), fill in what the gate says is missing, and cancel.
 * Every button is the route chat's handler calls, so a job dispatched
 * from here and one dispatched by "มอบหมาย T-… ให้ทีม แอร์" are the same
 * transaction and the same LINE to the technician.
 */
export default function SalesTickets({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const copy = t.dashboard.tickets;
  const statusLabel = (status: string) =>
    (copy.status as Record<string, string>)[status] ?? status;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [blockers, setBlockers] = useState<Record<string, string[]>>({});
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [canUpdate, setCanUpdate] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const [busyId, setBusyId] = useState("");
  const [showDone, setShowDone] = useState(false);
  const [target, setTarget] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string>("");
  const [draft, setDraft] = useState<Draft>({
    customer_name: "", customer_phone: "", service_address: "",
    serial_number: "", scheduled_date: "", scheduled_time: "",
  });
  const [confirmCancel, setConfirmCancel] = useState("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const load = useCallback(
    async (currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const headers = proxyHeaders(currentToken, license);
      const response = await fetch(`/api/phase2/licenses/${license}/tickets`, { headers });
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      const rows = (await response.json()) as Ticket[];
      setTickets(rows);

      // Only for the ones still waiting to go out; a dispatched ticket's
      // gate result is history and not worth a request each.
      const waiting = rows.filter((row) => row.status === "open");
      const found: Record<string, string[]> = {};
      await Promise.all(
        waiting.map(async (row) => {
          const check = await fetch(
            `/api/phase2/licenses/${license}/tickets/${row.id}/dispatch-check`,
            { headers },
          );
          if (check.ok) {
            const body = (await check.json()) as { missing?: string[] };
            if (body.missing?.length) found[row.id] = body.missing;
          } else {
            // A failed check must not read as "ready to dispatch": the
            // dispatcher would act on an answer nobody gave.
            found[row.id] = [copy.dispatchCheckFailed];
          }
        }),
      );
      setBlockers(found);
    },
    [token, licenseId, t, copy.dispatchCheckFailed],
  );

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
      setCanUpdate(permissions.has("ticket.update"));
      await load(session.token, license);
      const headers = proxyHeaders(session.token, license);
      const [techRes, teamRes] = await Promise.all([
        fetch(`/api/phase2/licenses/${license}/technicians`, { headers }),
        fetch(`/api/phase2/licenses/${license}/technician-teams`, { headers }),
      ]);
      setTechnicians(techRes.ok ? ((await techRes.json()) as Technician[]) : []);
      setTeams(teamRes.ok ? ((await teamRes.json()) as Team[]) : []);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, load, say, t]);

  /** One request, then reload; a 409 from the gate names what is missing. */
  async function send(ticket: Ticket, request: () => Promise<Response>, done: string) {
    setBusyId(ticket.id);
    try {
      const response = await request();
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as
          | { detail?: { missing?: string[]; blockers?: string[] } | string }
          | null;
        const detail = body && typeof body.detail === "object" ? body.detail : null;
        const missing = detail?.missing ?? detail?.blockers;
        say(
          missing?.length
            ? `${copy.blocked}: ${missing.join(", ")}`
            : `${copy.actionFailed} (${response.status})`,
          "error",
        );
        return false;
      }
      say(`${ticket.ticket_number} — ${done}`, "ok");
      await load();
      return true;
    } catch {
      say(copy.actionFailed, "error");
      return false;
    } finally {
      setBusyId("");
    }
  }

  function assign(ticket: Ticket) {
    const chosen = target[ticket.id] ?? "";
    if (!chosen) return;
    const [kind, ref] = chosen.split(":", 2);
    return send(
      ticket,
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/tickets/${ticket.id}/assign`, {
          method: "POST",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            target_type: kind === "team" ? "technician_team" : "technician",
            target_ref: ref,
          }),
        }),
      copy.assigned,
    );
  }

  function startEdit(ticket: Ticket) {
    setEditing(ticket.id);
    setDraft({
      customer_name: ticket.customer_name ?? "",
      customer_phone: ticket.customer_phone ?? "",
      service_address: ticket.service_address ?? "",
      serial_number: ticket.serial_number ?? "",
      scheduled_date: ticket.scheduled_date ?? "",
      scheduled_time: (ticket.scheduled_time ?? "").slice(0, 5),
    });
  }

  async function saveEdit(ticket: Ticket) {
    const fields: Record<string, string> = {};
    (Object.keys(draft) as (keyof Draft)[]).forEach((key) => {
      if (draft[key].trim()) fields[key] = draft[key].trim();
    });
    const ok = await send(
      ticket,
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/tickets/${ticket.id}`, {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify(fields),
        }),
      copy.saved,
    );
    if (ok) setEditing("");
  }

  async function cancel(ticket: Ticket) {
    const ok = await send(
      ticket,
      () =>
        fetch(`/api/phase2/licenses/${licenseId}/tickets/${ticket.id}/status`, {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({ status: "cancelled" }),
        }),
      copy.cancelled,
    );
    if (ok) setConfirmCancel("");
  }

  const visible = tickets.filter(
    (x) => showDone || (x.status !== "completed" && x.status !== "cancelled"),
  );
  const canDispatch = (x: Ticket) =>
    x.status !== "completed" && x.status !== "cancelled" && x.accept_status !== "accepted";
  const targetLabel = (x: Ticket) => {
    if (!x.assigned_to_ref) return "";
    if (x.assigned_target_type === "technician_team") {
      return teams.find((team) => team.id === x.assigned_to_ref)?.team_name ?? "";
    }
    return technicians.find((tech) => tech.id === x.assigned_to_ref)?.display_name ?? "";
  };

  return (
    <AppShell
      title={copy.title}
      back="/liff/sales"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <div className="actions" style={{ justifyContent: "flex-end" }}>
        <button
          type="button"
          className="btn"
          data-variant="quiet"
          onClick={() => setShowDone(!showDone)}
        >
          {showDone ? copy.hideDone : copy.showDone}
        </button>
      </div>
      {visible.length === 0 ? (
        <div className="empty">
          <p>{copy.empty}</p>
        </div>
      ) : (
        <ul className="list">
          {visible.map((ticket) => (
            <li key={ticket.id} className="card" data-stage={ticketStage(ticket.status)}>
              <div className="card-title">
                <span className="code">{ticket.ticket_number}</span>
                <span className="badge" data-stage={ticketStage(ticket.status)}>
                  {statusLabel(ticket.status)}
                </span>
              </div>
              <div className="card-meta">{ticket.issue_description}</div>
              {ticket.customer_name && (
                <div className="card-meta">
                  {ticket.customer_name}
                  {ticket.customer_phone ? ` · ${ticket.customer_phone}` : ""}
                </div>
              )}
              {ticket.service_address && <div className="card-meta">{ticket.service_address}</div>}
              {ticket.scheduled_date && (
                <div className="card-meta">
                  {copy.scheduled}: {formatWhen(ticket)}
                </div>
              )}
              {ticket.assigned_to_ref && (
                <div className="card-meta">
                  {copy.assignedTo}: {targetLabel(ticket) || "—"}
                  {ticket.accept_status === "accepted"
                    ? ` · ${copy.accepted}`
                    : ticket.accept_status === "rejected"
                      ? ` · ${copy.rejectedByTech}`
                      : ` · ${copy.awaitingAccept}`}
                </div>
              )}
              {blockers[ticket.id] && (
                <div className="card-meta" data-tone="error">
                  {copy.blocked}: {blockers[ticket.id].join(", ")}
                </div>
              )}

              {canUpdate && canDispatch(ticket) && editing !== ticket.id && (
                <div className="card-actions">
                  <select
                    aria-label={copy.assignTo}
                    value={target[ticket.id] ?? ""}
                    onChange={(e) => setTarget({ ...target, [ticket.id]: e.target.value })}
                  >
                    <option value="">{copy.assignTo}</option>
                    {teams.length > 0 && (
                      <optgroup label={copy.teamsGroup}>
                        {teams.map((team) => (
                          <option key={team.id} value={`team:${team.id}`}>
                            {team.team_name}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {technicians.length > 0 && (
                      <optgroup label={copy.techniciansGroup}>
                        {technicians.map((tech) => (
                          <option key={tech.id} value={`tech:${tech.id}`}>
                            {tech.display_name}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    disabled={busyId !== "" || !target[ticket.id]}
                    onClick={() => void assign(ticket)}
                  >
                    {busyId === ticket.id ? t.dashboard.related.saving : copy.assign}
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    disabled={busyId !== ""}
                    onClick={() => startEdit(ticket)}
                  >
                    {copy.edit}
                  </button>
                  {confirmCancel === ticket.id ? (
                    <>
                      <button
                        type="button"
                        className="btn"
                        data-variant="danger"
                        disabled={busyId !== ""}
                        onClick={() => void cancel(ticket)}
                      >
                        {copy.confirmCancel}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        onClick={() => setConfirmCancel("")}
                      >
                        {t.dashboard.related.cancelForm}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn"
                      data-variant="quiet"
                      disabled={busyId !== ""}
                      onClick={() => setConfirmCancel(ticket.id)}
                    >
                      {copy.cancel}
                    </button>
                  )}
                </div>
              )}

              {editing === ticket.id && (
                <dl className="fields">
                  <FieldRow label={copy.customer}>
                    {(id) => (
                      <input
                        id={id}
                        value={draft.customer_name}
                        onChange={(e) => setDraft({ ...draft, customer_name: e.target.value })}
                      />
                    )}
                  </FieldRow>
                  <FieldRow label={copy.phone}>
                    {(id) => (
                      <input
                        id={id}
                        type="tel"
                        inputMode="tel"
                        value={draft.customer_phone}
                        onChange={(e) => setDraft({ ...draft, customer_phone: e.target.value })}
                      />
                    )}
                  </FieldRow>
                  <FieldRow label={copy.address}>
                    {(id) => (
                      <textarea
                        id={id}
                        rows={2}
                        value={draft.service_address}
                        onChange={(e) => setDraft({ ...draft, service_address: e.target.value })}
                      />
                    )}
                  </FieldRow>
                  <FieldRow label={copy.serial}>
                    {(id) => (
                      <input
                        id={id}
                        value={draft.serial_number}
                        onChange={(e) => setDraft({ ...draft, serial_number: e.target.value })}
                      />
                    )}
                  </FieldRow>
                  <FieldRow label={copy.scheduledDate}>
                    {(id) => (
                      <input
                        id={id}
                        type="date"
                        value={draft.scheduled_date}
                        onChange={(e) => setDraft({ ...draft, scheduled_date: e.target.value })}
                      />
                    )}
                  </FieldRow>
                  <FieldRow label={copy.scheduledTime}>
                    {(id) => (
                      <input
                        id={id}
                        type="time"
                        value={draft.scheduled_time}
                        onChange={(e) => setDraft({ ...draft, scheduled_time: e.target.value })}
                      />
                    )}
                  </FieldRow>
                  <div className="actions">
                    <button
                      type="button"
                      className="btn"
                      data-variant="quiet"
                      disabled={busyId !== ""}
                      onClick={() => setEditing("")}
                    >
                      {t.dashboard.related.cancelForm}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      data-variant="primary"
                      disabled={busyId !== ""}
                      onClick={() => void saveEdit(ticket)}
                    >
                      {busyId === ticket.id ? t.dashboard.related.saving : t.dashboard.related.save}
                    </button>
                  </div>
                </dl>
              )}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
