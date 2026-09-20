"use client";

import { useCallback, useState, type ReactNode } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

/**
 * Doing one thing to several rows.
 *
 * Owner, 20 ก.ย. 2569: "อัพเดตสถานะแบบหลายรายการ … เช่น อัพเดตจากลูกค้า
 * มุ่งหวังเป็นยืนยันหลายๆคน หรือการลบลูกค้าหรือดีล … ทีละหลาย record".
 * The shape is the one every list tool uses (ui-ux-pro-max, Data Entry ›
 * Bulk Actions: "checkbox column + action bar"): a way into selection
 * mode, a checkbox on each row, and one bar that names how many are
 * chosen and what can be done to them. The bar sits at the bottom of the
 * screen where a thumb is, not at the top where the rows scrolled away
 * from.
 *
 * What runs for each row is the SAME request the row's own button sends —
 * there is no bulk endpoint to keep in step with the single one, and the
 * parity checker sees the same URLs it already knows.
 */
export function useSelection() {
  const [on, setOn] = useState(false);
  const [ids, setIds] = useState<Set<string>>(() => new Set());

  const toggle = useCallback((id: string) => {
    setIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const select = useCallback((wanted: string[]) => setIds(new Set(wanted)), []);
  const clear = useCallback(() => setIds(new Set()), []);
  const leave = useCallback(() => {
    setOn(false);
    setIds(new Set());
  }, []);
  const enter = useCallback(() => setOn(true), []);

  return { on, ids, toggle, select, clear, enter, leave, count: ids.size };
}

/** The checkbox on a row. 44px of hit area around a 22px box. */
export function SelectCheck({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
}) {
  return (
    <label className="select-check">
      <input type="checkbox" checked={checked} onChange={onChange} aria-label={label} />
      <span aria-hidden="true" />
    </label>
  );
}

/**
 * The bar. `children` are the actions; they come from the page because
 * only the page knows which verbs its rows take.
 */
export function BulkBar({
  count,
  shown,
  onSelectAll,
  onClear,
  onDone,
  busy,
  children,
}: {
  count: number;
  /** How many rows are on screen, for "เลือกทั้งหมดที่แสดง". */
  shown: number;
  onSelectAll: () => void;
  onClear: () => void;
  onDone: () => void;
  busy?: boolean;
  children: ReactNode;
}) {
  const { t } = useLanguage();
  const copy = t.dashboard.list;
  return (
    <div className="bulk-bar" role="region" aria-label={copy.selectMode}>
      {/* One atomic sentence for the live region — "3 selected" — rather
          than a bare number (ui-ux-pro-max, Contextual Live Badge Updates). */}
      <p className="bulk-count" aria-live="polite">
        {copy.selected.replace("{n}", String(count))}
      </p>
      <div className="bulk-actions">
        {count < shown ? (
          <button type="button" className="btn" data-variant="quiet" onClick={onSelectAll} disabled={busy}>
            {copy.selectAllShown}
          </button>
        ) : (
          <button type="button" className="btn" data-variant="quiet" onClick={onClear} disabled={busy || count === 0}>
            {copy.clearSelection}
          </button>
        )}
        {children}
        <button type="button" className="btn" data-variant="quiet" onClick={onDone} disabled={busy}>
          {copy.selectDone}
        </button>
      </div>
    </div>
  );
}

/**
 * Run one request per row, a few at a time, and count. Never stops at
 * the first failure: the person asked for twenty, and "18 of 20, these
 * two did not" is the answer they can act on — "failed" is not.
 */
export async function runEach<T>(
  rows: T[],
  each: (row: T) => Promise<boolean>,
  width = 4,
): Promise<{ ok: T[]; failed: T[] }> {
  const ok: T[] = [];
  const failed: T[] = [];
  let next = 0;
  async function worker() {
    while (next < rows.length) {
      const row = rows[next++];
      try {
        (await each(row)) ? ok.push(row) : failed.push(row);
      } catch {
        failed.push(row);
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(width, rows.length) }, worker));
  return { ok, failed };
}

/** "ทำสำเร็จ 18 จาก 20 รายการ" — or the shorter sentence when all went. */
export function useBulkSummary() {
  const { t } = useLanguage();
  return (ok: number, total: number) =>
    ok === total
      ? t.dashboard.list.bulkAllDone.replace("{total}", String(total))
      : t.dashboard.list.bulkResult.replace("{ok}", String(ok)).replace("{total}", String(total));
}
