"use client";

import { useCallback, useEffect, useRef, type ReactNode } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

/**
 * A panel that opens over the page, for work that is occasional.
 *
 * Owner, 20 ก.ย. 2569: the bulk-add and import tools sat open on the list
 * pages — a sample table, a file picker and a paste box, in front of
 * everyone, every visit, for a job most people do once. They belong
 * behind a button.
 *
 * This is not `ConfirmDialog`. That one is an `alertdialog` for a yes/no
 * question, it traps only buttons, and it focuses the SAFE choice because
 * a stray Enter there deletes something. A sheet holds a form: it has to
 * trap inputs and links as well, and the first field is the right place
 * to start rather than the cancel button.
 *
 * What it owns, so three callers do not each get one of them wrong:
 *  - Escape and a click on the veil close it;
 *  - Tab stays inside while it is open;
 *  - focus goes in on open and back to the button that opened it on close
 *    — a keyboard that lands at the top of the document has lost its place;
 *  - the page behind does not scroll under the panel on a phone.
 */
const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), ' +
  'textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export function Sheet({
  open,
  title,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** For a sheet holding a sample table. */
  wide?: boolean;
}) {
  const { t } = useLanguage();
  const panelRef = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  const close = useCallback(() => {
    onClose();
    // Back to whatever opened it. Without this a keyboard resumes at the
    // top of the document and the person has to find their place again.
    returnTo.current?.focus();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    returnTo.current = document.activeElement as HTMLElement | null;
    const first = panelRef.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panelRef.current)?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    // The list behind is long; without this the page scrolls under the
    // panel while a thumb is trying to scroll the panel itself.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
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
  }, [open, close]);

  if (!open) return null;
  const titleId = "sheet-title";

  return (
    <div className="sheet-veil" onClick={close}>
      <div
        className="sheet-panel"
        data-wide={wide ? "true" : undefined}
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sheet-head">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="sheet-close"
            onClick={close}
            aria-label={t.common.close}
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}
