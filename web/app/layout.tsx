import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "helia Regression Lab",
  description: "Hardware performance history and layer-level regression analysis for heliaPROFILER.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
