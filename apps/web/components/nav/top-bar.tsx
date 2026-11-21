import Link from "next/link";

import { Logo } from "@/components/brand/logo";

/**
 * Mobile-only top bar. On medium screens and up the brand sits at the top of
 * the sidebar instead.
 */
export function TopBar() {
  return (
    <header className="sticky top-0 z-30 flex h-12 items-center border-b border-border bg-background/85 px-4 backdrop-blur-md md:hidden">
      <Link href="/dashboard" aria-label="ChargeWaze home">
        <Logo />
      </Link>
    </header>
  );
}
