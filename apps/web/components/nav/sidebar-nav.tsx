"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { isActive, NAV_ITEMS, type NavItem } from "@/lib/nav";

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
  const main = NAV_ITEMS.filter((item) => !item.secondary);
  const secondary = NAV_ITEMS.filter((item) => item.secondary);
  return (
    <nav aria-label="Primary" className="flex flex-1 flex-col justify-between gap-1 p-3">
      <ul className="flex flex-col gap-0.5">
        {main.map((item) => (
          <SidebarLink key={item.href} item={item} active={isActive(pathname, item.href)} />
        ))}
      </ul>
      <ul className="flex flex-col gap-0.5">
        {secondary.map((item) => (
          <SidebarLink key={item.href} item={item} active={isActive(pathname, item.href)} />
        ))}
      </ul>
    </nav>
  );
}

function SidebarLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <li>
      <Link
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
          active
            ? "bg-accent font-medium text-foreground"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        )}
      >
        <Icon aria-hidden className="size-4 shrink-0" strokeWidth={active ? 2.25 : 1.75} />
        <span>{item.label}</span>
      </Link>
    </li>
  );
}
