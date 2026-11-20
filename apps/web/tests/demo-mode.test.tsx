import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyticsSearch,
  apiQuery,
  parseAnalyticsParams,
} from "@/components/analytics/params";
import { DemoBanner } from "@/components/demo/demo-banner";
import { DemoSignIn } from "@/components/demo/demo-sign-in";
import { env } from "@/lib/env";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const signInWithPassword = vi.fn();
vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({ auth: { signInWithPassword } }),
}));

const VEHICLE = "3f2b6c1e-9a0d-4c7e-8b1a-2d3e4f5a6b7c";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("demo env", () => {
  it("is off unless NEXT_PUBLIC_DEMO_MODE is true", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "");
    expect(env.demoMode).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "TRUE");
    expect(env.demoMode).toBe(true);
  });

  it("needs both demo credentials", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_EMAIL", "demo@example.com");
    vi.stubEnv("NEXT_PUBLIC_DEMO_PASSWORD", "");
    expect(env.demoCredentials).toBeNull();
    vi.stubEnv("NEXT_PUBLIC_DEMO_PASSWORD", "public-demo");
    expect(env.demoCredentials).toEqual({
      email: "demo@example.com",
      password: "public-demo",
    });
  });
});

describe("DemoBanner", () => {
  it("says the data is simulated and read-only except planning", () => {
    render(<DemoBanner enabled />);
    const banner = screen.getByTestId("demo-banner");
    expect(banner).toHaveTextContent("Public demo.");
    expect(banner).toHaveTextContent("simulated");
    expect(banner).toHaveTextContent("You can plan new trips");
  });

  it("renders nothing on a live deployment", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
    const { container } = render(<DemoBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("follows NEXT_PUBLIC_DEMO_MODE by default", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
    render(<DemoBanner />);
    expect(screen.getByTestId("demo-banner")).toBeInTheDocument();
  });
});

describe("DemoSignIn", () => {
  beforeEach(() => {
    signInWithPassword.mockReset();
    router.push.mockReset();
    router.refresh.mockReset();
  });

  it("signs in to the demo account and opens the dashboard", async () => {
    signInWithPassword.mockResolvedValue({ error: null });
    render(<DemoSignIn email="demo@example.com" password="public-demo" />);

    fireEvent.click(screen.getByRole("button", { name: "Explore the demo" }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/dashboard"));
    expect(signInWithPassword).toHaveBeenCalledWith({
      email: "demo@example.com",
      password: "public-demo",
    });
    expect(router.refresh).toHaveBeenCalled();
  });

  it("shows the error and stays put when sign-in fails", async () => {
    signInWithPassword.mockResolvedValue({
      error: new Error("Invalid login credentials"),
    });
    render(<DemoSignIn email="demo@example.com" password="wrong" />);

    fireEvent.click(screen.getByRole("button", { name: "Explore the demo" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The demo account couldn't sign in: Invalid login credentials",
    );
    expect(router.push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Explore the demo" })).toBeEnabled();
  });
});

describe("analytics simulated default", () => {
  it("leaves simulated data out on a live deployment", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
    const params = parseAnalyticsParams({ vehicle: VEHICLE });
    expect(params.includeSimulated).toBe(false);
    expect(apiQuery(params, VEHICLE).include_simulated).toBe(false);
    expect(analyticsSearch({ ...params, includeSimulated: true })).toContain(
      "simulated=1",
    );
    expect(analyticsSearch(params)).not.toContain("simulated");
  });

  it("includes simulated data in the demo, and simulated=0 still leaves it out", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
    const params = parseAnalyticsParams({ vehicle: VEHICLE });
    expect(params.includeSimulated).toBe(true);
    expect(apiQuery(params, VEHICLE).include_simulated).toBe(true);
    expect(analyticsSearch(params)).not.toContain("simulated");

    const realOnly = analyticsSearch({ ...params, includeSimulated: false });
    expect(realOnly).toContain("simulated=0");
    const back = Object.fromEntries(new URLSearchParams(realOnly.slice(1)));
    expect(parseAnalyticsParams(back).includeSimulated).toBe(false);
  });
});
