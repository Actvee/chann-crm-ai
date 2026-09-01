"use client";

import { useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { PickerOption, SearchablePicker } from "./_searchable-picker";

export type CreateField = {
  name: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  type?: "text" | "tel" | "email" | "select";
  options?: PickerOption[];
  /** Shown in the picker's search box when nothing is chosen yet. */
  searchHint?: string;
};

/**
 * Adding a record from the list that shows them.
 *
 * The list pages could promote, advance and issue — everything except
 * create the thing in the first place. Someone who opened the dashboard
 * to add a customer had to go back to LINE and type a sentence, which is
 * a strange thing for a screen full of customers to make you do.
 *
 * Collapsed until asked for: the common case is looking something up,
 * and a form permanently occupying the top of the list pushes the
 * records themselves below the fold.
 */
export function InlineCreateForm({
  title,
  fields,
  busy,
  onSubmit,
}: {
  title: string;
  fields: CreateField[];
  busy: boolean;
  onSubmit: (values: Record<string, string>) => Promise<void>;
}) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});

  const missing = fields.filter(
    (field) => field.required && !(values[field.name] ?? "").trim(),
  );

  async function submit() {
    await onSubmit(
      Object.fromEntries(
        Object.entries(values).map(([key, value]) => [key, value.trim()]),
      ),
    );
    // Cleared only on the way out: leaving the values in place after a
    // failure means the person does not retype what they just typed.
    setValues({});
    setOpen(false);
  }

  if (!open) {
    return (
      <div className="actions" style={{ margin: "0 0 16px" }}>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          onClick={() => setOpen(true)}
        >
          {title}
        </button>
      </div>
    );
  }

  return (
    <section className="section" style={{ marginBottom: 16 }}>
      <div className="section-head">
        <h2>{title}</h2>
      </div>
      <dl className="fields">
        {fields.map((field) => (
          <div className="field-row" key={field.name}>
            <dt>{field.label}</dt>
            <dd>
              {field.type === "select" ? (
                // Searchable, not a native select: a shop with a real
                // customer list cannot scroll to find someone, and on a
                // phone the list closes the moment you look away.
                <SearchablePicker
                  options={field.options ?? []}
                  value={values[field.name] ?? ""}
                  placeholder={field.searchHint}
                  onChange={(next) =>
                    setValues({ ...values, [field.name]: next })
                  }
                />
              ) : (
                <input
                  type={field.type === "tel" ? "tel" : "text"}
                  inputMode={field.type === "tel" ? "tel" : undefined}
                  value={values[field.name] ?? ""}
                  placeholder={field.placeholder}
                  onChange={(event) =>
                    setValues({ ...values, [field.name]: event.target.value })
                  }
                />
              )}
            </dd>
          </div>
        ))}
        <div className="actions">
          <button
            type="button"
            className="btn"
            data-variant="quiet"
            onClick={() => setOpen(false)}
            disabled={busy}
          >
            {t.common.cancel}
          </button>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            onClick={() => void submit()}
            disabled={busy || missing.length > 0}
          >
            {busy ? t.dashboard.saving : t.common.save}
          </button>
        </div>
      </dl>
    </section>
  );
}
