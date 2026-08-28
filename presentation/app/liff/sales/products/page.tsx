import ProductList from "./ProductList";

export const dynamic = "force-dynamic";

export default function SalesProductListPage() {
  return <ProductList liffId={process.env.NEXT_PUBLIC_LIFF_SALES_ID ?? ""} />;
}
