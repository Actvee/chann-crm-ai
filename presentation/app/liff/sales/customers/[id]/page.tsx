import CustomerDetail from "./CustomerDetail";

export const dynamic = "force-dynamic";

export default async function CustomerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <CustomerDetail
      liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""}
      customerId={id}
    />
  );
}
