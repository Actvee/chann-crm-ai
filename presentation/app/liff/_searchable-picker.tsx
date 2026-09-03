"use client";

import { useMemo, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

export type PickerOption = {
  value: string;
  label: string;
  /** Extra text to match on but not display — a code, a phone number. */
  keywords?: string;
};

/**
 * Choosing one record out of many.
 *
 * A native select is fine for four options and useless for four hundred:
 * a shop with a real customer list cannot scroll to find someone, and on
 * a phone the list closes if you look away. This filters as you type and
 * shows what is left.
 *
 * Matches on the label AND on hidden keywords, because people search by
 * the thing they have to hand — a phone number off a missed call, a deal
 * code from a chat message — not by the name the list happens to show.
 */
export function SearchablePicker({
  id,
  options,
  value,
  placeholder,
  onChange,
}: {
  /** From a FieldRow label; without one the search hint names the box. */
  id?: string;
  options: PickerOption[];
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  const chosen = options.find((option) => option.value === value);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options.slice(0, 30);
    return options
      .filter((option) =>
        `${option.label} ${option.keywords ?? ""}`.toLowerCase().includes(needle),
      )
      .slice(0, 30);
  }, [options, query]);

  // Already chosen: show the choice and a way out, rather than a search
  // box that looks like nothing has been selected.
  if (chosen && !open) {
    return (
      <div className="picker-chosen">
        <span>{chosen.label}</span>
        <button
          type="button"
          className="btn"
          data-variant="quiet"
          onClick={() => {
            onChange("");
            setQuery("");
            setOpen(true);
          }}
        >
          {t.common.change}
        </button>
      </div>
    );
  }

  return (
    <div className="picker">
      <input
        id={id}
        aria-label={id ? undefined : placeholder}
        type="search"
        value={query}
        placeholder={placeholder}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      {open && (
        <ul className="picker-options">
          {matches.length === 0 ? (
            <li className="picker-empty">{t.dashboard.noMatch}</li>
          ) : (
            matches.map((option) => (
              <li key={option.value}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(option.value);
                    setQuery("");
                    setOpen(false);
                  }}
                >
                  {option.label}
                </button>
              </li>
            ))
          )}
          {/* Said plainly rather than silently truncating: a list that
              stops at thirty with no explanation looks like the record
              is not there. */}
          {options.length > matches.length && matches.length === 30 && (
            <li className="picker-empty">{t.dashboard.keepTyping}</li>
          )}
        </ul>
      )}
    </div>
  );
}
