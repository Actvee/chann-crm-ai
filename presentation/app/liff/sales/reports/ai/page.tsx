import AiReports from "./AiReports";

export const dynamic = "force-dynamic";

/** Phase 17 — ask for any report in plain language; the answer is a
 *  table with a bar per row and files to keep. */
export default function SalesAiReportsPage() {
  return <AiReports liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
