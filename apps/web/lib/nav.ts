import {
  BarChart3,
  CarFront,
  LayoutDashboard,
  type LucideIcon,
  Route,
  UserRound,
  Zap,
} from "lucide-react";

/**
 * Primary navigation items shared by the desktop sidebar and the mobile menu.
 *
 * Keeping this list in one place guarantees the two surfaces never drift.
 * `secondary` items sit apart at the bottom of the desktop sidebar.
 */
export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  secondary?: boolean;
};

export const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/trips", label: "Trip Planner", icon: Route },
  { href: "/charging", label: "Charging", icon: Zap },
  { href: "/garage", label: "Garage", icon: CarFront },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/profile", label: "Profile", icon: UserRound, secondary: true },
];

/** True when `pathname` is `href` or one of its sub-routes. */
export function isActive(pathname: string | null, href: string): boolean {
  return pathname !== null && (pathname === href || pathname.startsWith(`${href}/`));
}
