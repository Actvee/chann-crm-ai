import SalesTickets from "./SalesTickets";

export const dynamic = "force-dynamic";

export default function SalesTicketsPage() {
  return <SalesTickets liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
