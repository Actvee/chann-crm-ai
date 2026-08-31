import CustomerTickets from "./CustomerTickets";

export const dynamic = "force-dynamic";

export default function CustomerTicketsPage() {
  return (
    <CustomerTickets liffId={process.env.NEXT_PUBLIC_LIFF_CUSTOMER_ID ?? ""} />
  );
}
