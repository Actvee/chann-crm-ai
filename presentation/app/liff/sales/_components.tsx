"use client";

import Link from "next/link";
import Script from "next/script";
import { ReactNode } from "react";

import { LIFF_SDK_SRC, Membership } from "./_lib";

/**
 * The shell every Sales dashboard page sits in.
 *
 * It exists mainly for the back link. Pages open inside the LINE in-app
 * browser, which has no address bar and no back button of its own, so
 * without this a person who taps into Deals from the menu has no way back
 * except closing the whole window and starting again.
 */
export function AppShell({
  title,
  back = "/liff/sales",
  liffId,
  onReady,
  onSdkError,
  status,
  statusTone,
  children,
}: {
  title: string;
  back?: string | null;
  liffId: string;
  onReady: () => void;
  onSdkError: () => void;
  status?: string;
  statusTone?: "ok" | "error";
  children: ReactNode;
}) {
  return (
    <>
      <Script src={LIFF_SDK_SRC} strategy="afterInteractive" onReady={onReady} onError={onSdkError} />
      <div className="shell">
        <header className="topbar">
          {back && (
            <Link className="backlink" href={back} aria-label="กลับไปหน้าเมนู">
              ←
            </Link>
          )}
          <h1>{title}</h1>
        </header>
        <div className="page">
          {status ? (
            <p className="status" data-tone={statusTone} aria-live="polite">
              {status}
            </p>
          ) : (
            // Kept in the tree even when empty so screen readers keep
            // watching the same node for updates.
            <p className="status" aria-live="polite" />
          )}
          {children}
        </div>
      </div>
      {/* liffId is threaded through for pages that need it in a data
          attribute for debugging; unused visually. */}
      <span hidden data-liff-id={liffId} />
    </>
  );
}

export function CompanyPicker({
  memberships,
  licenseId,
  onChange,
}: {
  memberships: Membership[];
  licenseId: string;
  onChange: (id: string) => void;
}) {
  // One company is the normal case and a select with a single option is
  // noise, so it only appears when there is a real choice to make.
  if (memberships.length <= 1) return null;
  return (
    <label className="field">
      <span>บริษัท</span>
      <select value={licenseId} onChange={(event) => onChange(event.target.value)}>
        {memberships.map((membership) => (
          <option key={membership.license_id} value={membership.license_id}>
            {membership.company_name} ({membership.license_code})
          </option>
        ))}
      </select>
    </label>
  );
}

export function Badge({ stage, label }: { stage: string; label: string }) {
  return (
    <span className="badge" data-stage={stage}>
      {label}
    </span>
  );
}

/** An empty screen is an invitation to act, so it always names the action. */
export function Empty({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <p>{message}</p>
      {action}
    </div>
  );
}

export function Count({ shown, total }: { shown: number; total: number }) {
  if (!total) return null;
  return (
    <p className="count">
      {shown === total ? `${total} รายการ` : `${shown} จาก ${total} รายการ`}
    </p>
  );
}
