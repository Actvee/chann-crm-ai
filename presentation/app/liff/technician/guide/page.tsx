import GuidePage from "../../_guide/GuidePage";

export const dynamic = "force-dynamic";

export default function TechnicianGuidePage() {
  return <GuidePage audience="technician" liffId={process.env.NEXT_PUBLIC_LIFF_TECHNICIAN_ID ?? ""} />;
}
