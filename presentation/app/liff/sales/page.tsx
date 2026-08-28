import SalesMenu from "./SalesMenu";

export const dynamic = "force-dynamic";

export default function SalesDashboardPage() {
  return <SalesMenu liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
