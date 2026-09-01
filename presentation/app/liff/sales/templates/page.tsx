import DocumentTemplates from "./DocumentTemplates";

export const dynamic = "force-dynamic";

export default function TemplatesPage() {
  return (
    <DocumentTemplates liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />
  );
}
