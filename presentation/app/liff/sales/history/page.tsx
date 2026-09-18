import AuditTrail from "./AuditTrail";

export const dynamic = "force-dynamic";

export default function SalesHistoryPage() {
  return <AuditTrail liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
