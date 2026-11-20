import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/brand/logo";

/**
 * Centered, chrome-free layout for sign-in / sign-up / callback screens.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <header className="flex h-14 items-center px-6">
        <Link href="/dashboard" aria-label="Chargewise home">
          <Logo />
        </Link>
      </header>
      <div className="flex flex-1 items-start justify-center px-4 pt-[12vh] pb-16">
        <div className="w-full max-w-sm">{children}</div>
      </div>
    </div>
  );
}
