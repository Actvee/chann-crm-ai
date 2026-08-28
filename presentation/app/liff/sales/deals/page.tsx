import DealList from "./DealList";

export const dynamic = "force-dynamic";

export default function SalesDealListPage() {
  return <DealList liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
