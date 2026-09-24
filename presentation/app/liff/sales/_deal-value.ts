/** What a deal is worth — the screens' copy of the one rule
 *  (`application/chann_app/services/deal_value.py`, and the SQL in
 *  `data/chann_data/repositories/deal_value.py`): the amount the
 *  salesperson typed when there is one — a typed 0 is a typed value —
 *  otherwise the line items. Pre-VAT, like every deal value on the
 *  platform (ruling 23). One copy, so the deal list, the deal page and the
 *  chat side panel cannot each decide for themselves (final review I4). */
export type DealValueInput = {
  amount?: string | number | null;
  products?: { qty?: number | string | null; quoted_unit_price?: string | number | null }[] | null;
};

export function dealValue(deal: DealValueInput): number {
  if (deal.amount != null && deal.amount !== "") return Number(deal.amount) || 0;
  return (deal.products ?? []).reduce(
    (sum, p) => sum + Number(p.qty ?? 0) * Number(p.quoted_unit_price ?? 0), 0,
  );
}

/** Whether the deal carries any value at all — neither a typed amount nor
 *  a line means "nothing to show", which is not the same as 0. */
export function hasDealValue(deal: DealValueInput): boolean {
  return (deal.amount != null && deal.amount !== "") || (deal.products ?? []).length > 0;
}
