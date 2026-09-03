import ApprovalQueue from "./ApprovalQueue";

export const dynamic = "force-dynamic";

export default function ApprovalsPage() {
  return <ApprovalQueue liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
