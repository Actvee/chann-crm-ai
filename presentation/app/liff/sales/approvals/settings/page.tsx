import ApprovalSettings from "./ApprovalSettings";

export const dynamic = "force-dynamic";

export default function ApprovalSettingsPage() {
  return <ApprovalSettings liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
