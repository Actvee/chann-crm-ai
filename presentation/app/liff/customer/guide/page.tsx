import GuidePage from "../../_guide/GuidePage";

export const dynamic = "force-dynamic";

export default function CustomerGuidePage() {
  return <GuidePage audience="customer" liffId={process.env.NEXT_PUBLIC_LIFF_CUSTOMER_ID ?? ""} />;
}
