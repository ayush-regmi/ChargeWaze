"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "@/lib/nav";

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
      className="fixed inset-x-0 bottom-0 z-40 flex border-t border-neutral-200 bg-white/95 backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/95 md:hidden"
    >
      {NAV_ITEMS.map((item) => {
        const active =
          pathname !== null &&
          (pathname === item.href || pathname.startsWith(`${item.href}/`));
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px]",
              active
                ? "text-emerald-700 dark:text-emerald-300"
                : "text-neutral-600 dark:text-neutral-400",
            )}
          >
            <span aria-hidden className="text-sm font-semibold">
              {item.icon}
            </span>
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
