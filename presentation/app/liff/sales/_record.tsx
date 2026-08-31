"use client";

import { ReactNode, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

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
  type?: "text" | "tel" | "email" | "textarea" | "number";
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
            <div className="field-row" key={field.name}>
              <dt>{field.label}</dt>
              <dd data-empty={!isEditingThis && (value == null || value === "")}>
                {isEditingThis ? (
                  field.type === "textarea" ? (
                    <textarea
                      rows={3}
                      value={draft[field.name] ?? ""}
                      placeholder={field.placeholder}
                      onChange={(event) =>
                        setDraft({ ...draft, [field.name]: event.target.value })
                      }
                    />
                  ) : (
                    <input
                      type={field.type ?? "text"}
                      value={draft[field.name] ?? ""}
                      placeholder={field.placeholder}
                      onChange={(event) =>
                        setDraft({ ...draft, [field.name]: event.target.value })
                      }
                    />
                  )
                ) : value == null || value === "" ? (
                  "—"
                ) : field.display ? (
                  field.display(value)
                ) : (
                  String(value)
                )}
              </dd>
            </div>
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
}: {
  stage?: string;
  title: string;
  subtitle?: ReactNode;
  badge?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="record-head" data-stage={stage}>
      <h1 className="record-title">
        {title}
        {badge}
      </h1>
      {subtitle && <p className="record-sub">{subtitle}</p>}
      {actions && <div className="actions">{actions}</div>}
    </div>
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
