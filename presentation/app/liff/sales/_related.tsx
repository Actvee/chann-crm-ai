"use client";

import { useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { FieldRow } from "../_field-row";
import { fullDateTime, shortDate } from "../_list-controls";
import { proxyHeaders } from "./_lib";
import { RelatedHeading } from "./_record";

type FollowUp = {
  id: string;
  due_date?: string | null;
  due_time?: string | null;
  notes?: string | null;
  status?: string | null;
};

/** Pending first and soonest first: the point of the panel is what is
 * still to happen. Shared by the first load and every reload after an
 * edit, so a row cannot jump position depending on how it arrived. */
function sortFollowUps(rows: FollowUp[]): FollowUp[] {
  const pending = (r: FollowUp) => (r.status === "pending" ? 0 : 1);
  return [...rows].sort(
    (a, b) =>
      pending(a) - pending(b) || (a.due_date ?? "").localeCompare(b.due_date ?? ""),
  );
}

type Note = {
  id: string;
  body?: string | null;
  created_at?: string | null;
  author_display_name?: string | null;
};

/**
 * What this record is connected to.
 *
 * The panel every CRM puts under a record — Zoho calls them related
 * lists — and the reason a record page is worth opening: the customer's
 * appointments and the notes about them, on the customer. Both existed
 * here only in chat, so opening a customer said nothing about the
 * appointment made with them ten minutes earlier.
 *
 * Loads on its own and shows what it can: a notes endpoint that fails
 * must not take the appointments down with it, and neither may block
 * the record above them.
 *
 * Every control is gated on the key its route checks (review C7): the
 * panel used to offer "เพิ่มนัด" to someone whose tap would 403.
 */
export function RelatedActivity({
  licenseId,
  token,
  entityType,
  entityId,
  permissions,
  readOnly = false,
}: {
  licenseId: string;
  token: string;
  entityType: "customer" | "deal" | "quote";
  entityId: string;
  permissions: Set<string>;
  /** A suspended shop: read everything, change nothing. */
  readOnly?: boolean;
}) {
  const { t, locale } = useLanguage();
  const [followUps, setFollowUps] = useState<FollowUp[] | null>(null);
  const [notes, setNotes] = useState<Note[] | null>(null);
  // Editing state. Appointments could be made and read from this panel
  // but never changed: no way to mark one done, cancel it, move it, or
  // add one — reported live (2 Sep) alongside the chat having the same
  // gap. Kept in this component rather than lifted, because nothing
  // above it needs to know.
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [form, setForm] = useState<{ date: string; time: string; note: string } | null>(null);
  // The appointment being edited in place. Was `movingId`, and meant
  // "create a replacement and cancel this one" — which is why a
  // postponed appointment used to come back with a new id and no
  // history (owner, 9 Sep: "นัดหมายเหมือนจะแก้ไข หรือลบไม่ได้"). It now
  // names the row that PATCH will change.
  const [editingId, setEditingId] = useState<string | null>(null);
  // Notes were read-only here: the panel showing a record's history was
  // the one place you could not add to it, and nothing anywhere could
  // correct a note — the Data Tier has had PATCH/DELETE since Phase 6
  // with no caller.
  const [noteForm, setNoteForm] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState<string | null>(null);

  const can = (key: string) => !readOnly && permissions.has(key);
  const canAddAppointment = can("followup.create");
  const canEditAppointment = can("followup.update");
  const canAddNote = can("note.create");
  const canEditNote = can("note.update");

  async function reloadNotes() {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/notes` +
        `?entity_type=${entityType}&entity_id=${entityId}`,
      { headers: proxyHeaders(token, licenseId) },
    );
    // Throw, so a caller's catch marks the action failed: silently
    // keeping the old list made a saved note look like it never landed.
    if (!response.ok) throw new Error(String(response.status));
    setNotes((await response.json()) as Note[]);
  }

  async function saveNote() {
    const body = (noteForm ?? "").trim();
    if (!body) return;
    setBusy(editingNote ?? "new-note");
    setFailed(null);
    try {
      const response = await fetch(
        editingNote
          ? `/api/phase2/licenses/${licenseId}/notes/${editingNote}`
          : `/api/phase2/licenses/${licenseId}/notes`,
        {
          method: editingNote ? "PATCH" : "POST",
          headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
          body: JSON.stringify(
            editingNote
              ? { body }
              : { entity_type: entityType, entity_id: entityId, body },
          ),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      setNoteForm(null);
      setEditingNote(null);
      await reloadNotes();
    } catch {
      setFailed(t.dashboard.related.actionFailed);
    } finally {
      setBusy(null);
    }
  }

  async function deleteNote(id: string) {
    setBusy(id);
    setFailed(null);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/notes/${id}`, {
        method: "DELETE",
        headers: proxyHeaders(token, licenseId),
      });
      if (!response.ok) throw new Error(String(response.status));
      await reloadNotes();
    } catch {
      setFailed(t.dashboard.related.actionFailed);
    } finally {
      setBusy(null);
    }
  }

  async function reload() {
    const headers = proxyHeaders(token, licenseId);
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/follow-ups` +
        `?entity_type=${entityType}&entity_id=${entityId}`,
      { headers },
    );
    if (!response.ok) throw new Error(String(response.status));
    const rows = (await response.json()) as FollowUp[];
    setFollowUps(sortFollowUps(rows));
  }

  /** Set a follow-up's status — the endpoint the Data Tier has always had. */
  async function setStatus(id: string, status: "completed" | "cancelled") {
    setBusy(id);
    setFailed(null);
    try {
      const response = await fetch(
        `/api/phase2/follow-ups/${id}/status?status_value=${status}`,
        { method: "PATCH", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) throw new Error(String(response.status));
      await reload();
    } catch {
      setFailed(t.dashboard.related.actionFailed);
    } finally {
      setBusy(null);
    }
  }

  /** Save the form — a new appointment, or an edit to an existing one.
   *
   * Editing used to be create-then-cancel over the only two endpoints
   * that existed, which worked and was wrong: the appointment changed
   * id on every postponement, so the reminder already pushed to LINE
   * pointed at a cancelled row and its audit trail restarted. One PATCH
   * now does it, which also removes the half-done state where the new
   * row was created and the old one failed to cancel (review C17).
   */
  async function saveAppointment(editing: string | null) {
    if (!form?.date) return;
    setBusy(editing ?? "new");
    setFailed(null);
    try {
      const response = editing
        ? await fetch(`/api/phase2/follow-ups/${editing}`, {
            method: "PATCH",
            headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
            body: JSON.stringify({
              due_date: form.date,
              due_time: form.time ? `${form.time}:00` : null,
              notes: form.note || null,
            }),
          })
        : await fetch(`/api/phase2/follow-ups`, {
            method: "POST",
            headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
            body: JSON.stringify({
              entity_type: entityType,
              entity_id: entityId,
              due_date: form.date,
              due_time: form.time ? `${form.time}:00` : null,
              notes: form.note || null,
            }),
          });
      if (!response.ok) throw new Error(String(response.status));
      setForm(null);
      setEditingId(null);
      await reload();
    } catch {
      setFailed(t.dashboard.related.actionFailed);
    } finally {
      setBusy(null);
    }
  }

  /** Remove an appointment outright.
   *
   * Deliberately separate from "ยกเลิกนัด" next to it, which sets the
   * status and keeps the row — that is what calling one off usually
   * means, and it stays the quiet default. This is for the appointment
   * booked against the wrong record. The confirm names the day and time
   * being removed, because the rows all look alike in this list, and it
   * points back at cancelling for anyone who wanted that instead.
   */
  async function removeAppointment(row: FollowUp) {
    const when =
      (shortDate(row.due_date, locale) || row.due_date || "") +
      (row.due_time ? ` ${String(row.due_time).slice(0, 5)}` : "");
    if (!window.confirm(t.dashboard.related.deleteAppointmentConfirm.replace("{when}", when)))
      return;
    setBusy(row.id);
    setFailed(null);
    try {
      const response = await fetch(`/api/phase2/follow-ups/${row.id}`, {
        method: "DELETE",
        headers: proxyHeaders(token, licenseId),
      });
      if (!response.ok) throw new Error(String(response.status));
      // The row being edited may be the one just removed.
      if (editingId === row.id) {
        setEditingId(null);
        setForm(null);
      }
      await reload();
    } catch {
      setFailed(t.dashboard.related.actionFailed);
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (!licenseId || !token || !entityId) return;
    const headers = proxyHeaders(token, licenseId);
    let cancelled = false;

    void (async () => {
      try {
        const response = await fetch(
          `/api/phase2/licenses/${licenseId}/follow-ups` +
            `?entity_type=${entityType}&entity_id=${entityId}`,
          { headers },
        );
        if (response.ok && !cancelled) {
          const rows = (await response.json()) as FollowUp[];
          setFollowUps(sortFollowUps(rows));
        } else if (!cancelled) {
          setFollowUps([]);
        }
      } catch {
        if (!cancelled) setFollowUps([]);
      }
    })();

    void (async () => {
      try {
        const response = await fetch(
          `/api/phase2/licenses/${licenseId}/notes` +
            `?entity_type=${entityType}&entity_id=${entityId}`,
          { headers },
        );
        if (response.ok && !cancelled) {
          setNotes((await response.json()) as Note[]);
        } else if (!cancelled) {
          setNotes([]);
        }
      } catch {
        if (!cancelled) setNotes([]);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [licenseId, token, entityType, entityId]);

  return (
    <>
      <RelatedHeading title={t.dashboard.related.appointments} count={followUps?.length ?? 0} />
      {/* The same shape as "เพิ่มสินค้า" on the deal and quote pages: a
          section with the action in its heading, and the form appearing
          inside it. The first version put a bare button under the empty
          state, which looked like a different product. */}
      {(canAddAppointment || form !== null) && (
        <section className="section" style={{ margin: "0 0 14px" }}>
          <div className="section-head">
            <h2>
              {editingId
                ? t.dashboard.related.editAppointment
                : t.dashboard.related.addAppointment}
            </h2>
            {form === null && canAddAppointment && (
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => setForm({ date: "", time: "", note: "" })}
                disabled={busy !== null}
              >
                {t.dashboard.related.addAppointment}
              </button>
            )}
          </div>
          {form !== null && (
            <dl className="fields">
              <FieldRow label={t.dashboard.related.appointmentDate}>
                {(id) => (
                  <input
                    id={id}
                    type="date"
                    value={form.date}
                    onChange={(e) => setForm({ ...form, date: e.target.value })}
                  />
                )}
              </FieldRow>
              <FieldRow label={t.dashboard.related.appointmentTime}>
                {(id) => (
                  <input
                    id={id}
                    type="time"
                    value={form.time}
                    onChange={(e) => setForm({ ...form, time: e.target.value })}
                  />
                )}
              </FieldRow>
              <FieldRow label={t.dashboard.related.appointmentNote}>
                {(id) => (
                  <input
                    id={id}
                    value={form.note}
                    onChange={(e) => setForm({ ...form, note: e.target.value })}
                  />
                )}
              </FieldRow>
              <div className="actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => {
                    setForm(null);
                    setEditingId(null);
                  }}
                  disabled={busy !== null}
                >
                  {t.dashboard.related.cancelForm}
                </button>
                <button
                  type="button"
                  className="btn"
                  data-variant="primary"
                  onClick={() => void saveAppointment(editingId)}
                  disabled={busy !== null || !form.date}
                >
                  {busy !== null ? t.dashboard.related.saving : t.dashboard.related.save}
                </button>
              </div>
            </dl>
          )}
        </section>
      )}
      {followUps === null ? null : followUps.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.related.noAppointments}</p>
        </div>
      ) : (
        <ul className="list">
          {followUps.map((row) => (
            <li
              key={row.id}
              className="card"
              data-stage={row.status === "pending" ? "proposed" : "won"}
            >
              <div className="card-title">
                {shortDate(row.due_date, locale) || row.due_date}
                {row.due_time ? ` ${String(row.due_time).slice(0, 5)}` : ""}
                {row.status && row.status !== "pending" && (
                  <span className="badge" data-stage="won" style={{ marginLeft: 8 }}>
                    {(t.dashboard.related.status as Record<string, string>)[row.status] ??
                      row.status}
                  </span>
                )}
              </div>
              {row.notes && <div className="card-meta">{row.notes}</div>}
              {row.status === "pending" && canEditAppointment && (
                <div className="card-actions">
                  {canEditAppointment && (
                    <>
                      {/* One PATCH, so followup.update alone is enough —
                          it used to need followup.create as well, because
                          "moving" meant booking a second appointment. */}
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        onClick={() => {
                          setEditingId(row.id);
                          setForm({
                            date: row.due_date ?? "",
                            time: String(row.due_time ?? "").slice(0, 5),
                            note: row.notes ?? "",
                          });
                        }}
                        disabled={busy !== null}
                      >
                        {t.dashboard.related.editAppointment}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        onClick={() => void setStatus(row.id, "completed")}
                        disabled={busy !== null}
                      >
                        {busy === row.id ? t.dashboard.related.saving : t.dashboard.related.markDone}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="quiet"
                        onClick={() => void setStatus(row.id, "cancelled")}
                        disabled={busy !== null}
                      >
                        {t.dashboard.related.cancelAppointment}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-variant="danger"
                        onClick={() => void removeAppointment(row)}
                        disabled={busy !== null}
                      >
                        {t.dashboard.related.deleteAppointment}
                      </button>
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {failed && <p className="card-meta" data-tone="error">{failed}</p>}

      <RelatedHeading title={t.dashboard.related.notes} count={notes?.length ?? 0} />
      {(canAddNote || noteForm !== null) && (
        <section className="section" style={{ margin: "0 0 14px" }}>
          <div className="section-head">
            <h2>{t.dashboard.related.addNote}</h2>
            {noteForm === null && canAddNote && (
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => {
                  setEditingNote(null);
                  setNoteForm("");
                }}
                disabled={busy !== null}
              >
                {t.dashboard.related.addNote}
              </button>
            )}
          </div>
          {noteForm !== null && (
            <dl className="fields">
              <FieldRow label={t.dashboard.related.noteBody}>
                {(id) => (
                  <textarea
                    id={id}
                    rows={3}
                    value={noteForm}
                    onChange={(e) => setNoteForm(e.target.value)}
                  />
                )}
              </FieldRow>
              <div className="actions">
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => {
                    setNoteForm(null);
                    setEditingNote(null);
                  }}
                  disabled={busy !== null}
                >
                  {t.dashboard.related.cancelForm}
                </button>
                <button
                  type="button"
                  className="btn"
                  data-variant="primary"
                  onClick={() => void saveNote()}
                  disabled={busy !== null || !noteForm.trim()}
                >
                  {busy !== null ? t.dashboard.related.saving : t.dashboard.related.save}
                </button>
              </div>
            </dl>
          )}
        </section>
      )}
      {notes === null ? null : notes.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.related.noNotes}</p>
        </div>
      ) : (
        <ul className="list">
          {notes.map((note) => (
            <li key={note.id} className="card">
              <div className="card-meta" style={{ whiteSpace: "pre-wrap" }}>
                {note.body ?? ""}
              </div>
              <div className="card-meta" style={{ fontSize: 12, color: "var(--ink-faint)" }}>
                {fullDateTime(note.created_at, locale)}
                {/* Who wrote it (review C18): the API now names the author. */}
                {note.author_display_name ? ` · ${note.author_display_name}` : ""}
              </div>
              {canEditNote && (
                <div className="card-actions">
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    onClick={() => {
                      setEditingNote(note.id);
                      setNoteForm(note.body ?? "");
                    }}
                    disabled={busy !== null}
                  >
                    {t.common.edit}
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-variant="quiet"
                    onClick={() => void deleteNote(note.id)}
                    disabled={busy !== null}
                  >
                    {busy === note.id ? t.dashboard.related.saving : t.common.delete}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
