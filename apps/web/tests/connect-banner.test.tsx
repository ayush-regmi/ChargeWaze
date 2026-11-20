import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectBanner, connectBanner } from "@/components/garage/connect-banner";

let search = "";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
}));

describe("ConnectBanner", () => {
  beforeEach(() => {
    search = "";
  });

  it.each([
    ["success", /vehicle connected/i, "status"],
    ["already_connected", /already connected/i, "status"],
    ["owned_elsewhere", /connected to another account/i, "alert"],
    ["denied", /connection cancelled/i, "status"],
    ["error", /couldn't connect your vehicle/i, "alert"],
  ])("renders the %s banner", (flag, text, role) => {
    search = `connect=${flag}`;
    render(<ConnectBanner />);
    expect(screen.getByRole(role)).toHaveTextContent(text);
  });

  it.each(["", "connect=", "connect=hacked", "connect=__proto__", "connect=toString"])(
    "renders nothing for %j",
    (query) => {
      search = query;
      const { container } = render(<ConnectBanner />);
      expect(container).toBeEmptyDOMElement();
    },
  );

  it("ignores unknown values in the pure helper", () => {
    expect(connectBanner(null)).toBeNull();
    expect(connectBanner("constructor")).toBeNull();
    expect(connectBanner("success")?.title).toBe("Vehicle connected");
    expect(connectBanner("owned_elsewhere")?.tone).toBe("error");
  });
});
