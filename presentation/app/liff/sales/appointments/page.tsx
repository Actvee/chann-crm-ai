import Appointments from "./Appointments";

export const dynamic = "force-dynamic";

export default function SalesAppointmentsPage() {
  return <Appointments liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
