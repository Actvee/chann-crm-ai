export const metadata = {
  title: "Chann CRM AI",
  description: "Multi-tenant CRM and field service platform on LINE",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="th">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>{children}</body>
    </html>
  );
}
