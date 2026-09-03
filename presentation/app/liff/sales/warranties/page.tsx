import SalesWarranties from "./SalesWarranties";

export const dynamic = "force-dynamic";

export default function SalesWarrantiesPage() {
  return <SalesWarranties liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
