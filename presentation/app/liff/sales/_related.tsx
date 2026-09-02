"use client";

import { useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { fullDateTime } from "../_list-controls";
import { proxyHeaders } from "./_lib";
import { RelatedHeading } from "./_record";

type FollowUp = {
  id: string;
  due_date?: string | null;
  due_time?: string | null;
  notes?: string | null;
  status?: string | null;
};

type Note = {
  id: string;
  body?: string | null;
  text?: string | null;
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
 */
export function RelatedActivity({
  licenseId,
  token,
  entityType,
  entityId,
}: {
  licenseId: string;
  token: string;
  entityType: "customer" | "deal" | "quote";
  entityId: string;
}) {
  const { t } = useLanguage();
  const [followUps, setFollowUps] = useState<FollowUp[] | null>(null);
  const [notes, setNotes] = useState<Note[] | null>(null);

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
          // Pending first and soonest first: the point of the panel is
          // what is still to happen.
          rows.sort((a, b) => {
            const pending = (r: FollowUp) => (r.status === "pending" ? 0 : 1);
            return pending(a) - pending(b)
              || (a.due_date ?? "").localeCompare(b.due_date ?? "");
          });
          setFollowUps(rows);
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
                {row.due_date}
                {row.due_time ? ` ${String(row.due_time).slice(0, 5)}` : ""}
                {row.status && row.status !== "pending" && (
                  <span className="badge" data-stage="won" style={{ marginLeft: 8 }}>
                    {(t.dashboard.related.status as Record<string, string>)[row.status] ??
                      row.status}
                  </span>
                )}
              </div>
              {row.notes && <div className="card-meta">{row.notes}</div>}
            </li>
          ))}
        </ul>
      )}

      <RelatedHeading title={t.dashboard.related.notes} count={notes?.length ?? 0} />
      {notes === null ? null : notes.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.related.noNotes}</p>
        </div>
      ) : (
        <ul className="list">
          {notes.map((note) => (
            <li key={note.id} className="card">
              <div className="card-meta" style={{ whiteSpace: "pre-wrap" }}>
                {note.body ?? note.text ?? ""}
              </div>
              <div className="card-meta" style={{ fontSize: 12, color: "var(--ink-faint)" }}>
                {fullDateTime(note.created_at)}
                {note.author_display_name ? ` · ${note.author_display_name}` : ""}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
