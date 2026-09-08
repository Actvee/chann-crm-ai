import MemberManagement from "./MemberManagement";

export const dynamic = "force-dynamic";

export default function SalesMemberManagementPage() {
  return (
    <MemberManagement liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />
  );
}
