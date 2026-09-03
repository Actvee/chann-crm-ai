import GuidePage from "../../_guide/GuidePage";

export const dynamic = "force-dynamic";

export default function SalesGuidePage() {
  return <GuidePage audience="sales" liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
