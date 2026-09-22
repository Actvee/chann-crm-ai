import InvoiceList from "./InvoiceList";

export const dynamic = "force-dynamic";

export default function SalesInvoicesPage() {
  return <InvoiceList liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
