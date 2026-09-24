"use client";

import Link from "next/link";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

/**
 * "ออกใบแจ้งหนี้": the one button that starts a bill, on every page that
 * can start one (quote, deal, customer, invoices).
 *
 * Owner, 24 ก.ย. 2569: "ในหน้าใบแจ้งหนี้ ปุ่มสร้างใบแจ้งหนี้ก็ไม่เหมือนใน
 * ใบเสนอราคา". The quote page's button is the model. It uses the verb chat
 * uses ("ออกใบแจ้งหนี้ Q-…"), the verb the deal and customer pages
 * already used, and a primary that is full width on a phone. The
 * invoices page said "สร้างใบแจ้งหนี้" in a compact button tucked into
 * the count line. One component means the label, shape and variant
 * cannot drift apart again (ui-ux-pro-max: consistency).
 */
export function CreateInvoiceButton({
  href,
  onClick,
  again = false,
  primary = true,
  disabled = false,
}: {
  /** A page that opens the invoices page's form links there. */
  href?: string;
  /** A page that raises the bill itself calls this. */
  onClick?: () => void;
  /** One has been raised already: "ออกใบแจ้งหนี้อีกใบ". */
  again?: boolean;
  /** False where something else on the page is the primary action. */
  primary?: boolean;
  disabled?: boolean;
}) {
  const { t } = useLanguage();
  const label = again ? t.dashboard.invoices.fromQuoteAgain : t.dashboard.invoices.create;
  const variant = primary ? "primary" : undefined;
  if (href) {
    return (
      <Link className="btn" data-variant={variant} href={href}>
        {label}
      </Link>
    );
  }
  return (
    <button type="button" className="btn" data-variant={variant} onClick={onClick} disabled={disabled}>
      {label}
    </button>
  );
}
