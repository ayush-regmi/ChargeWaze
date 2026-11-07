/**
 * Primary navigation items shared by the desktop sidebar and the mobile menu.
 *
 * Keeping this list in one place guarantees the two surfaces never drift.
 * Icons are kept inline as emoji placeholders for Phase 1; they are replaced
 * with Lucide icons (or similar) in Phase 2 once we pick an icon set.
 */
export type NavItem = {
  href: string;
  label: string;
  icon: string;
};

export const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { href: "/dashboard", label: "Dashboard", icon: "H" },
  { href: "/trips", label: "Trip Planner", icon: "T" },
  { href: "/charging", label: "Charging", icon: "C" },
  { href: "/garage", label: "Garage", icon: "G" },
  { href: "/analytics", label: "Analytics", icon: "A" },
  { href: "/profile", label: "Profile", icon: "P" },
];
