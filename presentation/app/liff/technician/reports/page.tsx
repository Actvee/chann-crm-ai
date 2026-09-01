import ServiceReports from "./ServiceReports";

export const dynamic = "force-dynamic";

export default function TechnicianReportsPage() {
  return (
    <ServiceReports
      liffId={process.env.NEXT_PUBLIC_LIFF_TECHNICIAN_ID ?? ""}
      audience="technician"
    />
  );
}
