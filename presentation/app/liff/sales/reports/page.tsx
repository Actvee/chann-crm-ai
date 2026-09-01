import ServiceReports from "../../technician/reports/ServiceReports";

export const dynamic = "force-dynamic";

export default function SalesReportsPage() {
  return (
    <ServiceReports
      liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""}
      audience="sales"
    />
  );
}
