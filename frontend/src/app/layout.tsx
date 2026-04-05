import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { GoogleAnalytics } from "@next/third-parties/google";
import { auth } from "@/auth";
import { SiteHeader } from "@/components/SiteHeader";
import { Footer } from "@/components/Footer";
import ServiceWorkerRegister from "./sw-register";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ?? "https://galloplab.com"
  ),
  title: "GallopLab - 競馬AI指数・期待値分析",
  description:
    "JRA競馬のAI指数・期待値を提供する競馬予測サービス。スピード指数・コース適性・騎手指数など14種の指数で合理的な馬券購入をサポート。",
  manifest: "/manifest.json",
  robots: { index: false, follow: false },
  alternates: {
    canonical: "https://galloplab.com",
  },
  icons: {
    icon: [
      { url: "/images/favicon/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/images/favicon/favicon-192x192.png", sizes: "192x192", type: "image/png" },
      { url: "/images/favicon/favicon-512x512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: { url: "/images/favicon/favicon-180x180.png", sizes: "180x180", type: "image/png" },
  },
  openGraph: {
    type: "website",
    locale: "ja_JP",
    siteName: "GallopLab",
    title: "GallopLab - 競馬AI指数・期待値分析",
    description: "JRA競馬のAI指数・期待値を提供する競馬予測サービス。スピード指数・コース適性・騎手指数など14種の指数で合理的な馬券購入をサポート。",
    images: [
      { url: "/images/og-image.png", width: 1200, height: 630, alt: "GallopLab - 競馬AI指数・期待値分析" },
      { url: "/images/logo.png", width: 512, height: 512 },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "GallopLab",
    description: "JRA-VAN公式データを活用した競馬AI指数・期待値分析サービス",
    images: ["/images/og-image.png"],
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "GallopLab",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#1a5c38",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const session = await auth();
  const isAdmin = session?.user?.role === "admin";

  return (
    <html
      lang="ja"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* スキップナビゲーション（キーボードユーザー向け） */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:bg-white focus:text-black focus:rounded focus:shadow-lg"
        >
          メインコンテンツへスキップ
        </a>

        {/* 共有ヘッダー（/ と /login では非表示） */}
        <SiteHeader isAdmin={isAdmin} />

        <div className="flex-1 flex flex-col">
          {children}
        </div>
        {process.env.NEXT_PUBLIC_PAID_MODE === "true" && <Footer />}
        <ServiceWorkerRegister />
        {process.env.NEXT_PUBLIC_GA_ID && (
          <GoogleAnalytics gaId={process.env.NEXT_PUBLIC_GA_ID} />
        )}
      </body>
    </html>
  );
}
