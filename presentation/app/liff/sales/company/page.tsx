import CompanyProfile from "./CompanyProfile";

export const dynamic = "force-dynamic";

export default function SalesCompanyProfilePage() {
  return <CompanyProfile liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
