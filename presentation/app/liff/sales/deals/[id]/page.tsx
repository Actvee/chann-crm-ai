import DealDetail from "./DealDetail";

export const dynamic = "force-dynamic";

export default async function DealDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <DealDetail
      liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""}
      dealId={id}
    />
  );
}
