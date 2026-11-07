import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SidebarNav } from "@/components/nav/sidebar-nav";
import { NAV_ITEMS } from "@/lib/nav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

describe("SidebarNav", () => {
  it("renders every primary nav item", () => {
    render(<SidebarNav />);
    for (const item of NAV_ITEMS) {
      expect(
        screen.getByRole("link", { name: new RegExp(item.label, "i") }),
      ).toHaveAttribute("href", item.href);
    }
  });

  it("marks the active route with aria-current", () => {
    render(<SidebarNav />);
    const dashboard = screen.getByRole("link", { name: /dashboard/i });
    expect(dashboard).toHaveAttribute("aria-current", "page");
  });
});
