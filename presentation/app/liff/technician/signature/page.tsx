import SignaturePage from "../../_signature/SignaturePage";

export const dynamic = "force-dynamic";

export default function TechnicianSignaturePage() {
  return <SignaturePage audience="technician" liffId={process.env.NEXT_PUBLIC_LIFF_TECHNICIAN_ID ?? ""} />;
}
