import SignaturePage from "../../_signature/SignaturePage";

export const dynamic = "force-dynamic";

export default function CustomerSignaturePage() {
  return <SignaturePage audience="customer" liffId={process.env.NEXT_PUBLIC_LIFF_CUSTOMER_ID ?? ""} />;
}
