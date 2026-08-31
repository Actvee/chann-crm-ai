import TechnicianTickets from "./TechnicianTickets";

export const dynamic = "force-dynamic";

export default function TechnicianTicketsPage() {
  return (
    <TechnicianTickets
      liffId={process.env.NEXT_PUBLIC_LIFF_TECHNICIAN_ID ?? ""}
    />
  );
}
