import TechnicianHome from "./TechnicianHome";

export const dynamic = "force-dynamic";

export default function TechnicianHomePage() {
  return (
    <TechnicianHome liffId={process.env.NEXT_PUBLIC_LIFF_TECHNICIAN_ID ?? ""} />
  );
}
