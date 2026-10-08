import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {title: "Research Workbench · 研究工作台", description: "从问题到证据，再到研究成果。"};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}

