import CustomerList from "./CustomerList";

export const dynamic = "force-dynamic";

export default function SalesCustomerListPage() {
  return <CustomerList liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
