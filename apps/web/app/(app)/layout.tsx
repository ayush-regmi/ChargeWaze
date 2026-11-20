import Link from "next/link";
import { Suspense, type ReactNode } from "react";

import { Logo } from "@/components/brand/logo";
import { DemoBanner } from "@/components/demo/demo-banner";
import { MobileNav, MobileNavLinks } from "@/components/nav/mobile-nav";
import { SidebarNav, SidebarNavLinks } from "@/components/nav/sidebar-nav";
import { TopBar } from "@/components/nav/top-bar";

/**
 * Chrome shared by every signed-in app route: a full-height sidebar on medium
 * screens and up; a slim top bar and a bottom tab bar on narrow screens. The
 * auth guard lives in `proxy.ts`. The navs read the URL, so they sit in
 * Suspense: dynamic routes like `/garage/[id]` can't prerender them.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-border bg-sidebar md:flex">
        <div className="flex h-14 items-center px-5">
          <Link href="/dashboard" aria-label="Chargewise home">
            <Logo />
          </Link>
        </div>
        <Suspense fallback={<SidebarNavLinks pathname={null} />}>
          <SidebarNav />
        </Suspense>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <DemoBanner />
        <main className="flex-1 px-4 pt-6 pb-28 md:px-10 md:pt-10 md:pb-16">{children}</main>
      </div>
      <Suspense fallback={<MobileNavLinks pathname={null} />}>
        <MobileNav />
      </Suspense>
    </div>
  );
}
