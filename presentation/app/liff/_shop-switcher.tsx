"use client";

import { useId, useState } from "react";

import { Membership } from "./_shared";

/**
 * Which of several shops this person is acting in.
 *
 * Owner walk, 3 Sep: one LINE account was staff at ร้านทดสอบ and a
 * customer of Dev Company, and the customer home opened in the staff
 * shop with nothing in it. The server stores the choice per OA (the
 * same store chat's "ใช้ร้าน X" writes), so the next open and the next
 * chat message both land in the chosen shop. Rendered only when there
 * is a choice to make.
 */
export function ShopSwitcher({
  token,
  audience,
  shops,
  current,
  label,
  onSwitched,
}: {
  token: string;
  audience: "customer" | "technician" | "sales";
  shops: Membership[];
  current: string;
  label: string;
  onSwitched: (licenseId: string) => void;
}) {
  const id = useId();
  const [busy, setBusy] = useState(false);

  async function choose(licenseId: string) {
    if (!licenseId || licenseId === current) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/liff/${audience}/active-shop`, {
        method: "PUT",
        headers: { "X-Liff-ID-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify({ license_id: licenseId }),
      });
      if (!response.ok) throw new Error(String(response.status));
      onSwitched(licenseId);
    } catch {
      // The page keeps the shop it had; the select snaps back below.
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="section">
      <dl className="fields">
        <div className="field-row">
          <dt>
            <label htmlFor={id}>{label}</label>
          </dt>
          <dd>
            <select
              id={id}
              value={current}
              disabled={busy}
              onChange={(e) => void choose(e.target.value)}
            >
              {shops.map((shop) => (
                <option key={shop.license_id} value={shop.license_id}>
                  {shop.company_name}
                  {shop.license_code ? ` (${shop.license_code})` : ""}
                </option>
              ))}
            </select>
          </dd>
        </div>
      </dl>
    </section>
  );
}
