import SignaturePage from "../../_signature/SignaturePage";

export const dynamic = "force-dynamic";

export default function SalesSignaturePage() {
  return <SignaturePage audience="sales" liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
