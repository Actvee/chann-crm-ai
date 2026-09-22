import Satisfaction from "./Satisfaction";

export const dynamic = "force-dynamic";

/** Round 20V — the satisfaction surveys, read back at last. */
export default function SalesSatisfactionPage() {
  return <Satisfaction liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
