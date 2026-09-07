import ServiceReports from "../../technician/reports/ServiceReports";

export const dynamic = "force-dynamic";

/** Review D4 (6 Sep 2026): the customer's own service reports — what was
 *  found and done, the site photos, the PDF once approved. The same
 *  component the technician and CS read; the server scopes the rows. */
export default function CustomerReportsPage() {
  return (
    <ServiceReports
      liffId={process.env.NEXT_PUBLIC_LIFF_CUSTOMER_ID ?? ""}
      audience="customer"
    />
  );
}
