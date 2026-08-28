import Link from "next/link";

export const dynamic = "force-dynamic";

/**
 * The Sales dashboard index — the page every other one links back to.
 *
 * This project is chat-first, and the LIFF pages that existed before were
 * reachable only by knowing their URL. This is the hub each new phase adds
 * a tile to, so a page shipped later is discoverable without anyone having
 * to remember it exists.
 */

const SECTIONS = [
  {
    href: "/liff/sales/customers",
    title: "ลูกค้า",
    description: "รายชื่อลูกค้าและลูกค้ามุ่งหวัง ค้นหาและยืนยันเป็นลูกค้า",
  },
  {
    href: "/liff/sales/deals",
    title: "ดีล",
    description: "ติดตามดีลและเปลี่ยนสถานะการขาย",
  },
  {
    href: "/liff/sales/quotes",
    title: "ใบเสนอราคา",
    description: "ดูตัวอย่างและออกเอกสาร PDF",
  },
  {
    href: "/liff/sales/products",
    title: "สินค้า",
    description: "รายการสินค้าและราคา",
  },
  {
    href: "/liff/sales/company",
    title: "ข้อมูลบริษัท",
    description: "ข้อมูลที่จะพิมพ์บนเอกสารถึงลูกค้า",
  },
  {
    href: "/liff/sales/roles",
    title: "ทีมและสิทธิ์",
    description: "บทบาทของทีมและการตั้งค่าบริษัท",
  },
];

export default function SalesDashboardPage() {
  return (
    <div className="shell">
      <header className="topbar">
        <h1>เมนูทีมขาย</h1>
      </header>
      <div className="page">
        <p style={{ color: "var(--ink-soft)", fontSize: 14.5, margin: "0 0 16px" }}>
          ทุกอย่างในนี้สั่งผ่านแชทได้เช่นกัน หน้านี้เหมาะกับงานที่ต้องกรอกหลายช่องหรือดูรายการยาว ๆ
        </p>

        <ul className="tiles">
          {SECTIONS.map((section) => (
            <li key={section.title}>
              <Link className="tile" href={section.href}>
                <h2>{section.title}</h2>
                <p>{section.description}</p>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
