"use client";

import Link from "next/link";
import { Children, ReactNode, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { FieldRow } from "../_field-row";
import { fullDateTime } from "../_list-controls";

/**
 * The record-detail building blocks, shared by customers, deals and quotes.
 *
 * Written once rather than per page because the behaviour that matters —
 * when a field is editable, what happens on save, what a person sees while
 * it saves — should not be able to differ between record types. Three
 * copies would drift, and the one that drifts is the one that silently
 * stops respecting a permission.
 */

export type FieldSpec = {
  name: string;
  label: string;
  /** Rendered value. Falls back to the raw value when omitted. */
  display?: (value: unknown) => ReactNode;
  /** Omit to make the field read-only regardless of permission. */
  editable?: boolean;
  type?: "text" | "tel" | "email" | "textarea" | "number" | "date";
  placeholder?: string;
};

/**
 * A titled group of fields, editable in place when the person holds the
 * permission it names.
 *
 * Editing is whole-section rather than per-field. Per-field pencils mean a
 * tap target per row and a save round trip per value; a section that
 * becomes a form is one decision, one save, and no half-applied record if
 * the connection drops mid-way.
 */
export function FieldSection({
  title,
  fields,
  record,
  canEdit,
  onSave,
  action,
}: {
  title: string;
  fields: FieldSpec[];
  record: Record<string, unknown> | null;
  canEdit: boolean;
  onSave?: (changes: Record<string, string | null>) => Promise<void>;
  action?: ReactNode;
}) {
  const { t } = useLanguage();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  // Reset whenever the record changes underneath: a stale draft written
  // over freshly loaded values would silently undo someone else's edit.
  useEffect(() => {
    setEditing(false);
  }, [record]);

  const editableFields = fields.filter((f) => f.editable);
  const showEdit = canEdit && Boolean(onSave) && editableFields.length > 0;

  function beginEdit() {
    const next: Record<string, string> = {};
    for (const field of editableFields) {
      const value = record?.[field.name];
      next[field.name] = value == null ? "" : String(value);
    }
    setDraft(next);
    setEditing(true);
  }

  async function save() {
    if (!onSave) return;
    setSaving(true);
    try {
      // Only what actually changed, and empty means "clear this" rather
      // than "leave it alone" — the two are different intentions and the
      // API distinguishes them.
      const changes: Record<string, string | null> = {};
      for (const field of editableFields) {
        const before = record?.[field.name];
        const beforeText = before == null ? "" : String(before);
        const after = draft[field.name] ?? "";
        if (after !== beforeText) {
          changes[field.name] = after.trim() === "" ? null : after;
        }
      }
      if (Object.keys(changes).length > 0) {
        await onSave(changes);
      }
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {action}
        {showEdit && !editing && (
          <button type="button" className="btn" data-variant="quiet" onClick={beginEdit}>
            {t.common.edit}
          </button>
        )}
        {editing && (
          <span style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className="btn"
              data-variant="quiet"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              {t.common.cancel}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              onClick={() => void save()}
              disabled={saving}
            >
              {saving ? t.dashboard.saving : t.common.save}
            </button>
          </span>
        )}
      </div>

      <dl className="fields">
        {fields.map((field) => {
          const value = record?.[field.name];
          const isEditingThis = editing && field.editable;
          return (
            <FieldRow
              key={field.name}
              label={field.label}
              empty={!isEditingThis && (value == null || value === "")}
            >
              {isEditingThis
                ? (id) =>
                    field.type === "textarea" ? (
                      <textarea
                        id={id}
                        rows={3}
                        value={draft[field.name] ?? ""}
                        placeholder={field.placeholder}
                        onChange={(event) =>
                          setDraft({ ...draft, [field.name]: event.target.value })
                        }
                      />
                    ) : (
                      <input
                        id={id}
                        type={field.type ?? "text"}
                        value={draft[field.name] ?? ""}
                        placeholder={field.placeholder}
                        onChange={(event) =>
                          setDraft({ ...draft, [field.name]: event.target.value })
                        }
                      />
                    )
                : value == null || value === ""
                  ? "—"
                  : field.display
                    ? field.display(value)
                    : String(value)}
            </FieldRow>
          );
        })}
      </dl>
    </section>
  );
}

/** The header block naming what this record is. */
export function RecordHead({
  stage,
  title,
  subtitle,
  badge,
  actions,
  createdAt,
  updatedAt,
}: {
  stage?: string;
  title: string;
  subtitle?: ReactNode;
  /** System timestamps, shown on every record the way any CRM does. */
  createdAt?: string | null;
  updatedAt?: string | null;
  badge?: ReactNode;
  actions?: ReactNode;
}) {
  const { t, locale } = useLanguage();
  return (
    <div className="record-head" data-stage={stage}>
      <h1 className="record-title">
        {title}
        {badge}
      </h1>
      {subtitle && <p className="record-sub">{subtitle}</p>}
      {(createdAt || updatedAt) && (
        <p className="record-sub" style={{ fontSize: 12, color: "var(--ink-faint)" }}>
          {createdAt && `${t.dashboard.list.createdAt} ${fullDateTime(createdAt, locale)}`}
          {createdAt && updatedAt && updatedAt !== createdAt && " · "}
          {updatedAt && updatedAt !== createdAt &&
            `${t.dashboard.list.updatedAt} ${fullDateTime(updatedAt, locale)}`}
        </p>
      )}
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

/**
 * The record's stage, and the moves allowed from here — on its own.
 *
 * Owner, 22 ก.ย. 2569: "ปุ่มออกใบแจ้งหนี้ กับปุ่มสถานะ … ตอนนี้ปนกันมั่วไป
 * หมด ให้แยกระหว่างส่วนอัพเดตสถานะ กับปุ่มอื่น". Changing what a record IS
 * and doing something WITH it are two different decisions; one row of
 * buttons made them look like a menu of equals, and three of them were
 * styled primary at once (ui-ux-pro-max: one primary CTA per screen,
 * destructive actions visually separated, related items grouped).
 *
 * A move that ends the record badly (rejected, lost) is `danger`; the
 * rest are plain. The primary button of the page belongs to the actions
 * block beside this one, never here.
 */
export function StatusSection({
  title,
  current,
  moves,
  note,
  children,
}: {
  title: string;
  /** The badge for the stage the record is in now. */
  current: ReactNode;
  moves?: ReactNode;
  /** Why there is nothing to press — a final stage, or no permission. */
  note?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="section record-status">
      <div className="section-head">
        <h2>{title}</h2>
        <span className="record-status-now">{current}</span>
      </div>
      {(moves || note || children) && (
        <div className="section-body">
          {moves && <div className="actions record-status-moves">{moves}</div>}
          {note && <p className="card-meta record-status-note">{note}</p>}
          {children}
        </div>
      )}
    </section>
  );
}

/**
 * What this record lets you DO: issue the document, bill it, open what
 * came of it.
 *
 * Round 21E (owner, 24 ก.ย. 2569: "ปุ่มก็ชิดขอบออกแบบไม่ดี"). The rows sat
 * straight in the section, which has no padding of its own, so every
 * button touched the box's edge. They now sit in a `.section-body` with
 * the head's 16px gutter. The one primary action has a row of its own,
 * full width on a phone. The page names it, and no other button decides
 * for itself that it is primary. The secondary actions share the row
 * below it, and anything that undoes the record sits under a rule
 * (ui-ux-pro-max: primary-action, touch-spacing, destructive-emphasis,
 * spacing-scale).
 */
export function RecordActions({
  title,
  primary,
  notice,
  children,
  danger,
  note,
}: {
  title: string;
  /** The one thing to press next, if there is one. */
  primary?: ReactNode;
  /** A line that states a fact first, e.g. "ออกใบแจ้งหนี้ INV-… แล้ว". */
  notice?: ReactNode;
  children?: ReactNode;
  danger?: ReactNode;
  note?: ReactNode;
}) {
  return (
    <section className="section record-actions">
      <div className="section-head">
        <h2>{title}</h2>
      </div>
      <div className="section-body">
        {notice}
        {primary && <div className="actions record-primary">{primary}</div>}
        {Children.toArray(children).length > 0 && (
          <div className="actions record-secondary">{children}</div>
        )}
        {note && <p className="card-meta record-status-note">{note}</p>}
        {danger && <div className="actions record-danger">{danger}</div>}
      </div>
    </section>
  );
}

/**
 * The records on the other end of this one, each a link.
 *
 * Owner, 22 ก.ย. 2569: "จากใบแจ้งหนี้ก็ควรกดไปที่ record ที่เกี่ยวข้องได้
 * ด้วย". Every page already knew the codes; it printed them as text, so
 * the way from a bill back to its deal was the search box.
 */
export function RelatedLinks({
  items,
}: {
  items: { href: string; label: string; code?: string | null }[];
}) {
  const shown = items.filter((item) => item.href);
  if (shown.length === 0) return null;
  return (
    <nav className="record-links" aria-label={shown[0].label}>
      {shown.map((item) => (
        <Link key={item.href + item.label} className="record-link" href={item.href}>
          <span className="record-link-label">{item.label}</span>
          {item.code && <span className="code">{item.code}</span>}
        </Link>
      ))}
    </nav>
  );
}

export function RelatedHeading({ title, count }: { title: string; count: number }) {
  return (
    <div className="related-head">
      <h2>{title}</h2>
      <span className="related-count">{count}</span>
    </div>
  );
}
