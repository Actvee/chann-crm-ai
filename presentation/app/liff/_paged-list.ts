"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { proxyHeaders } from "./sales/_lib";

/** How long a person keeps typing before the list goes and asks. */
const DEBOUNCE_MS = 300;
/** Rows per request. Small enough to arrive fast on a phone. */
const PAGE = 50;

export type PagedList<T> = {
  rows: T[];
  /** How many match the current search — not how many the shop has. */
  total: number | null;
  query: string;
  setQuery: (value: string) => void;
  busy: boolean;
  /** True while the FIRST page of a new search is in flight. */
  searching: boolean;
  hasMore: boolean;
  loadMore: () => void;
  /** Refetch from the top, keeping the search and the filters. */
  reload: () => Promise<void>;
  /** Drop a row locally after deleting it, without a round trip. */
  setRows: (next: (rows: T[]) => T[]) => void;
};

/**
 * A list that is searched and paged by the SERVER.
 *
 * Every list screen used to fetch rows and filter them in JavaScript. That
 * is fine while a shop is small, and becomes a data-loss bug the moment a
 * list is capped: round 20j put a ceiling of 500 on customers and deals,
 * so a shop with 800 customers could type the 600th name, be told "ไม่พบ",
 * and believe it. The cap did not slow the page down — it hid records
 * (20 ก.ย. 2569).
 *
 * Three things this owns so that six screens do not each get them wrong:
 *
 *  - **the debounce**, so typing does not cost a request per keystroke;
 *  - **the stale-answer guard**, because two requests in flight can land
 *    out of order and the slower one wins — the chat inbox shipped that
 *    bug twice (round 20i) and it is the same shape here: a request
 *    sequence, checked after every await, not just the last one;
 *  - **the total**, which comes from `X-Total-Count` and is counted by the
 *    Data tier through the same filter as the page, so "แสดง 50 จาก 1,240"
 *    describes the search rather than the shop.
 */
export function usePagedList<T>({
  token,
  licenseId,
  path,
  params,
  ready = true,
  pageSize = PAGE,
  onError,
}: {
  token: string;
  licenseId: string;
  /** Everything after /api/phase2/, e.g. `licenses/L1/customers`. */
  path: string;
  /** Server-side filters besides the search term (stage, status, category). */
  params?: Record<string, string | undefined>;
  ready?: boolean;
  pageSize?: number;
  onError: (message: string, status?: number) => void;
}): PagedList<T> {
  const [rows, setRows] = useState<T[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [searching, setSearching] = useState(false);

  // The filters, flattened, so an object rebuilt on every render does not
  // look like a change and refetch the list forever.
  const filters = useMemo(
    () =>
      Object.entries(params ?? {})
        .filter(([, value]) => value !== undefined && value !== "")
        .map(([key, value]) => `${key}=${value}`)
        .sort()
        .join("&"),
    [params],
  );

  // Only the newest request may write to the list. Checked after EVERY
  // await, because both the fetch and the json() are suspension points and
  // an older answer can arrive between them.
  const seq = useRef(0);

  const fetchPage = useCallback(
    async (offset: number, append: boolean) => {
      if (!ready || !token || !licenseId) return;
      const mine = ++seq.current;
      setBusy(true);
      if (!append) setSearching(true);
      try {
        const search = new URLSearchParams(
          filters ? Object.fromEntries(new URLSearchParams(filters)) : {},
        );
        if (query.trim()) search.set("q", query.trim());
        search.set("limit", String(pageSize));
        if (offset) search.set("offset", String(offset));
        const response = await fetch(`/api/phase2/${path}?${search.toString()}`, {
          headers: proxyHeaders(token, licenseId),
          cache: "no-store",
        });
        if (mine !== seq.current) return;
        if (!response.ok) {
          onError("", response.status);
          return;
        }
        const body = (await response.json()) as T[];
        if (mine !== seq.current) return;
        const said = response.headers.get("X-Total-Count");
        setTotal(said === null ? null : Number(said));
        setRows((current) => (append ? [...current, ...body] : body));
      } catch {
        if (mine === seq.current) onError("");
      } finally {
        if (mine === seq.current) {
          setBusy(false);
          setSearching(false);
        }
      }
    },
    [filters, licenseId, onError, pageSize, path, query, ready, token],
  );

  // What the person typed becomes the search a moment after they stop.
  useEffect(() => {
    const id = window.setTimeout(() => setQuery(typed), DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [typed]);

  // A new search, or a new filter, starts again from the top.
  useEffect(() => {
    void fetchPage(0, false);
  }, [fetchPage]);

  return {
    rows,
    total,
    query: typed,
    setQuery: setTyped,
    busy,
    searching,
    hasMore: total !== null && rows.length < total,
    loadMore: () => void fetchPage(rows.length, true),
    reload: () => fetchPage(0, false),
    setRows: (next) => setRows((current) => next(current)),
  };
}
