"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** Asking before something destructive, and saying what it will do.
 *
 * Every destructive action in this dashboard used `window.confirm`, which
 * on a phone is a system alert with English OK/Cancel, no way to show the
 * record's code or what else is affected, no severity, no busy state and
 * no focus management (owner, 17 ก.ย. 2569: "ตอนคำถาม confirm ก่อนลบก็ด้วย
 * … UX ไม่ดีเลย").
 *
 * What this shows instead, in the order a person needs it:
 *   1. the verb and the thing, by name AND code — "ลบ สมชาย ใจดี (C-2026-0001)"
 *   2. what else it touches, when anything else is touched
 *   3. whether it can be undone — this product archives, it does not erase
 *   4. two buttons whose labels are the actions, never "OK"
 *
 * Accessibility: role="alertdialog" with the title and body wired through
 * aria-labelledby/aria-describedby; focus starts on CANCEL (the safe
 * choice, never the destructive one); Tab is trapped; Escape cancels; the
 * confirm button carries aria-busy while the work runs.
 */
export type ConfirmRequest = {
  /** "ลบลูกค้า" — the verb, used in the title and on the button. */
  action: string;
  /** "สมชาย ใจดี" — what it will happen to. */
  target: string;
  /** "C-2026-0001" — shown beside the name so the right row is obvious. */
  code?: string;
  /** What else this touches: "ดีล 2 รายการยังอยู่ · ประวัติงานยังอยู่". */
  affects?: string[];
  /** "เก็บถาวร กู้คืนได้" — or, when it truly cannot be undone, say so. */
  reversible?: string;
  /** Anything that is genuinely irreversible turns the dialog red-hot. */
  permanent?: boolean;
  confirmLabel?: string;
  cancelLabel?: string;
};

type Copy = { cancel: string; confirm: string; permanent: string };

export function useConfirm() {
  const [request, setRequest] = useState<
    (ConfirmRequest & { resolve: (ok: boolean) => void }) | null
  >(null);

  function ask(req: ConfirmRequest): Promise<boolean> {
    return new Promise((resolve) => setRequest({ ...req, resolve }));
  }

  function close(ok: boolean) {
    request?.resolve(ok);
    setRequest(null);
  }

  return { request, ask, close };
}

export function ConfirmDialog({
  request,
  onClose,
  copy,
  busy = false,
}: {
  request: (ConfirmRequest & { resolve: (ok: boolean) => void }) | null;
  onClose: (ok: boolean) => void;
  copy: Copy;
  busy?: boolean;
}): ReactNode {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // The safe choice takes focus. Opening a destructive dialog with the
  // destructive button focused turns a stray Enter into a deletion.
  useEffect(() => {
    if (request) cancelRef.current?.focus();
  }, [request]);

  useEffect(() => {
    if (!request) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
        "button:not(:disabled)",
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [request, onClose]);

  if (!request) return null;
  const titleId = "confirm-title";
  const bodyId = "confirm-body";

  return (
    <div className="confirm-veil" onClick={() => !busy && onClose(false)}>
      <div
        className="confirm-panel"
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        data-permanent={request.permanent ? "yes" : undefined}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className="confirm-title">
          {request.action} {request.target}
          {request.code ? <span className="confirm-code"> ({request.code})</span> : null}
        </h2>
        <div id={bodyId} className="confirm-body">
          {(request.affects ?? []).map((line) => (
            <p key={line} className="confirm-affects">· {line}</p>
          ))}
          <p className="confirm-reversible" data-permanent={request.permanent ? "yes" : undefined}>
            {request.permanent ? copy.permanent : request.reversible}
          </p>
        </div>
        <div className="confirm-actions">
          <button
            type="button"
            className="btn"
            ref={cancelRef}
            onClick={() => onClose(false)}
            disabled={busy}
          >
            {request.cancelLabel ?? copy.cancel}
          </button>
          <button
            type="button"
            className="btn"
            data-variant="danger"
            onClick={() => onClose(true)}
            disabled={busy}
            aria-busy={busy || undefined}
          >
            {request.confirmLabel ?? `${request.action} ${copy.confirm}`}
          </button>
        </div>
      </div>
    </div>
  );
}
