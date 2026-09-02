"use client";

import { useMemo, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

export type SortOption<T> = {
  key: string;
  label: string;
  compare: (a: T, b: T) => number;
};

/**
 * Sorting and date filtering for a list of records.
 *
 * What every CRM list view has and this one did not: a shop with three
 * hundred deals cannot find "the ones from last week" by scrolling, and
 * "newest first" is the order anyone opening the page actually wants.
 *
 * Done client-side on the loaded list. The lists here are per-tenant and
 * small enough that shipping them and filtering in the browser is faster
 * than a round trip per keystroke, and it keeps the Data Tier's list
 * endpoints unchanged.
 */
export function useListControls<T extends { created_at?: string | null }>(
  rows: T[],
  sorts: SortOption<T>[],
  defaultSort?: string,
) {
  const [sortKey, setSortKey] = useState(defaultSort ?? sorts[0]?.key ?? "");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const visible = useMemo(() => {
    let out = rows;
    if (from) {
      out = out.filter((row) => (row.created_at ?? "") >= from);
    }
    if (to) {
      // Inclusive of the whole day: "to 6 Sep" means through 6 Sep, and
      // a timestamp of 2026-09-06T14:00 is after the bare date string.
      out = out.filter((row) => (row.created_at ?? "").slice(0, 10) <= to);
    }
    const sort = sorts.find((option) => option.key === sortKey);
    if (sort) {
      out = [...out].sort(sort.compare);
    }
    return out;
  }, [rows, from, to, sortKey, sorts]);

  return { visible, sortKey, setSortKey, from, setFrom, to, setTo };
}

export function ListControls({
  sorts,
  sortKey,
  onSort,
  from,
  to,
  onFrom,
  onTo,
}: {
  sorts: { key: string; label: string }[];
  sortKey: string;
  onSort: (key: string) => void;
  from: string;
  to: string;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
}) {
  const { t } = useLanguage();
  return (
    <div className="list-controls">
      <label>
        <span>{t.dashboard.list.sort}</span>
        <select value={sortKey} onChange={(event) => onSort(event.target.value)}>
          {sorts.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t.dashboard.list.from}</span>
        <input type="date" value={from} onChange={(event) => onFrom(event.target.value)} />
      </label>
      <label>
        <span>{t.dashboard.list.to}</span>
        <input type="date" value={to} onChange={(event) => onTo(event.target.value)} />
      </label>
      {(from || to) && (
        <button
          type="button"
          className="btn"
          data-variant="quiet"
          onClick={() => {
            onFrom("");
            onTo("");
          }}
        >
          {t.dashboard.list.clear}
        </button>
      )}
    </div>
  );
}

/** Newest first — the default any list should open with. */
export function byNewest<T extends { created_at?: string | null }>(a: T, b: T) {
  return (b.created_at ?? "").localeCompare(a.created_at ?? "");
}

export function byOldest<T extends { created_at?: string | null }>(a: T, b: T) {
  return (a.created_at ?? "").localeCompare(b.created_at ?? "");
}

/** A created timestamp as a short local date, for a list row. */
export function shortDate(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("th-TH", { day: "numeric", month: "short", year: "2-digit" });
}

/** A created timestamp with time, for a record header. */
export function fullDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("th-TH", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}
