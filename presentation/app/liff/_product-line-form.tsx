"use client";

import { useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { FieldRow } from "./_field-row";
import { proxyHeaders } from "./_shared";

export type CatalogueProduct = {
  id: string;
  product_id: string;
  product_name: string;
  unit_price?: string | number | null;
  category?: string | null;
};

/**
 * Adding a line to a deal or a quote.
 *
 * Shared because the two are the same job: a name, a quantity and a
 * price. Two copies would drift, and the one that drifted would be the
 * one that forgot to carry the catalogue price.
 *
 * The catalogue comes first. Typing a name that already exists means
 * retyping a price the shop has already entered, and getting it slightly
 * wrong — which produces a document quoting a number nobody approved.
 * Free text is still allowed, because a one-off line ("ค่าติดตั้ง") is a
 * legitimate thing to charge for and does not belong in the catalogue.
 */
export function ProductLineForm({
  licenseId,
  token,
  busy,
  initial,
  onCancel,
  onSubmit,
}: {
  licenseId: string;
  token: string;
  busy: boolean;
  /** An existing line being corrected, rather than a new one. */
  initial?: { name: string; qty: number; price: string };
  onCancel: () => void;
  onSubmit: (line: { name: string; qty: number; price: string }) => Promise<void>;
}) {
  const { t } = useLanguage();
  const [catalogue, setCatalogue] = useState<CatalogueProduct[]>([]);
  const [pickedId, setPickedId] = useState("");
  const [name, setName] = useState(initial?.name ?? "");
  const [qty, setQty] = useState(String(initial?.qty ?? 1));
  const [price, setPrice] = useState(initial?.price ?? "");

  useEffect(() => {
    if (!licenseId || !token) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(
          `/api/phase2/licenses/${licenseId}/products`,
          { headers: proxyHeaders(token, licenseId) },
        );
        if (!response.ok) return;
        const rows = (await response.json()) as CatalogueProduct[];
        if (!cancelled) setCatalogue(rows);
      } catch {
        // An empty catalogue degrades to the free-text fields, which is
        // visible and still usable. Blocking the form on this would stop
        // someone adding a line at all.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [licenseId, token]);

  function pick(productId: string) {
    setPickedId(productId);
    const product = catalogue.find((row) => row.id === productId);
    if (!product) {
      // "Something else" — clear so the free-text fields start empty
      // rather than carrying the last pick's values.
      setName("");
      setPrice("");
      return;
    }
    setName(product.product_name);
    setPrice(product.unit_price != null ? String(product.unit_price) : "");
  }

  const canSubmit = name.trim() !== "" && price.trim() !== "";

  return (
    <dl className="fields">
      {catalogue.length > 0 && !initial && (
        <FieldRow label={t.product.title}>
          {(id) => (
            <select
              id={id}
              value={pickedId}
              onChange={(event) => pick(event.target.value)}
            >
              <option value="">{t.dashboard.deals.pickProduct}</option>
              {catalogue.map((product) => (
                <option key={product.id} value={product.id}>
                  {product.product_name}
                  {product.unit_price != null
                    ? ` — ${Number(product.unit_price).toLocaleString("th-TH", {
                        minimumFractionDigits: 2,
                      })}`
                    : ""}
                </option>
              ))}
            </select>
          )}
        </FieldRow>
      )}

      <FieldRow
        label={catalogue.length > 0 ? t.dashboard.deals.orTypeName : t.product.title}
      >
        {(id) => (
          <input
            id={id}
            value={name}
            placeholder={t.dashboard.deals.oneOffExample}
            onChange={(event) => {
              setName(event.target.value);
              // Typing over a pick means it is no longer that product;
              // leaving the select showing it would misreport what is
              // about to be saved.
              if (pickedId) setPickedId("");
            }}
          />
        )}
      </FieldRow>

      <FieldRow label={t.dashboard.deals.qty}>
        {(id) => (
          <input
            id={id}
            inputMode="numeric"
            value={qty}
            onChange={(event) => setQty(event.target.value)}
          />
        )}
      </FieldRow>

      <FieldRow label={t.dashboard.products.price}>
        {(id) => (
          <input
            id={id}
            inputMode="decimal"
            value={price}
            placeholder="0.00"
            onChange={(event) => setPrice(event.target.value)}
          />
        )}
      </FieldRow>

      {/* The line total, so a wrong quantity is caught here rather than
          on a document the customer is reading. */}
      {canSubmit && (
        <div className="field-row">
          <dt>{t.dashboard.deals.subtotal}</dt>
          <dd>
            <strong>
              {(Number(qty || 0) * Number(price || 0)).toLocaleString("th-TH", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </strong>
          </dd>
        </div>
      )}

      <div className="actions">
        <button
          type="button"
          className="btn"
          data-variant="quiet"
          onClick={onCancel}
          disabled={busy}
        >
          {t.common.cancel}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          disabled={busy || !canSubmit}
          onClick={() =>
            void onSubmit({
              name: name.trim(),
              qty: Number(qty) || 1,
              price: price.trim(),
            })
          }
        >
          {busy ? t.dashboard.saving : t.common.save}
        </button>
      </div>
    </dl>
  );
}
