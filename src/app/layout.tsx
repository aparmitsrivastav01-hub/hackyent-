import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "OpportunityOS — Find your next opportunity", description: "AI-powered discovery for hackathons and internships." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
