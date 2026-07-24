import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "집결정 AI | 설명 가능한 주거비 비교",
  description: "청년 1인 가구를 위한 근거 중심 주거 의사결정 에이전트",
};

export const viewport: Viewport = {
  themeColor: "#f4f1e9",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
