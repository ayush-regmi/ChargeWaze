"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { isActive, NAV_ITEMS } from "@/lib/nav";

/**
 * Mobile bottom tab bar. Shows a condensed version of the primary nav.
 */
export function MobileNav() {
  return <MobileNavLinks pathname={usePathname()} />;
}

/**
 * The links themselves. `pathname` is null in the Suspense fallback the layout
 * renders while the URL isn't known yet (prerendering a dynamic route), so no
 * item is highlighted until it streams in.
 */
export function MobileNavLinks({ pathname }: { pathname: string | null }) {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 flex border-t border-border bg-background/85 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
    >
      {NAV_ITEMS.map((item) => {
        const active = isActive(pathname, item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex flex-1 flex-col items-center gap-1 pt-2 pb-1.5 text-[10px] font-medium transition-colors",
              active ? "text-foreground" : "text-muted-foreground",
            )}
          >
            <Icon aria-hidden className="size-5" strokeWidth={active ? 2.25 : 1.75} />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
