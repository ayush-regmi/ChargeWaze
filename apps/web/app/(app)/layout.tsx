import { Suspense, type ReactNode } from "react";

import { MobileNav, MobileNavLinks } from "@/components/nav/mobile-nav";
import { SidebarNav, SidebarNavLinks } from "@/components/nav/sidebar-nav";
import { TopBar } from "@/components/nav/top-bar";

/**
 * Chrome shared by every signed-in app route: top bar, desktop sidebar on
 * medium screens and up, mobile tab bar on narrow screens. Slice 2 wraps this
 * layout with an auth guard via `proxy.ts`. The navs read the URL, so they sit
 * in Suspense: dynamic routes like `/garage/[id]` can't prerender them.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <TopBar />
      <div className="flex flex-1">
        <aside className="hidden w-60 shrink-0 border-r border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950 md:block">
          <Suspense fallback={<SidebarNavLinks pathname={null} />}>
            <SidebarNav />
          </Suspense>
        </aside>
        <main className="flex-1 p-4 pb-24 md:p-8 md:pb-8">
          {children}
        </main>
      </div>
      <Suspense fallback={<MobileNavLinks pathname={null} />}>
        <MobileNav />
      </Suspense>
    </div>
  );
}
