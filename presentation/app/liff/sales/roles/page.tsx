import RoleManagement from "./RoleManagement";

export const dynamic = "force-dynamic";

export default function SalesRoleManagementPage() {
  return (
    <RoleManagement liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />
  );
}
