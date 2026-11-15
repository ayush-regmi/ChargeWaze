"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "@/lib/nav";

/**
 * Desktop sidebar. Rendered inside `app/(app)/layout.tsx`.
 */
export function SidebarNav() {
  return <SidebarNavLinks pathname={usePathname()} />;
}

/**
 * The links themselves. `pathname` is null in the Suspense fallback the layout
 * renders while the URL isn't known yet (prerendering a dynamic route), so no
 * item is highlighted until it streams in.
 */
export function SidebarNavLinks({ pathname }: { pathname: string | null }) {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-1 p-4">
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
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
              active
                ? "bg-emerald-600/10 text-emerald-700 dark:text-emerald-300"
                : "text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800",
            )}
          >
            <span
              aria-hidden
              className="inline-flex h-6 w-6 items-center justify-center rounded-sm border border-current text-xs"
            >
              {item.icon}
            </span>
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
