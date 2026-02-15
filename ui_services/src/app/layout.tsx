import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MidJab — Smart Resume Pipeline",
  description: "Upload your resume, match with jobs, and auto-tailor applications.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-950 text-gray-100 antialiased">
        {children}
      </body>
    </html>
  );
}
