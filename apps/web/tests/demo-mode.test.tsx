import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyticsSearch,
  apiQuery,
  parseAnalyticsParams,
} from "@/components/analytics/params";
import {
  DemoAccountBanner,
  DemoBanner,
} from "@/components/demo/demo-banner";
import { DemoSignIn } from "@/components/demo/demo-sign-in";
import { isDemoEmail } from "@/lib/demo";
import { env } from "@/lib/env";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const signInWithPassword = vi.fn();
vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({ auth: { signInWithPassword } }),
}));

const sessionIsDemo = vi.fn<() => Promise<boolean>>();
vi.mock("@/lib/session-api", () => ({ sessionIsDemo: () => sessionIsDemo() }));

const VEHICLE = "3f2b6c1e-9a0d-4c7e-8b1a-2d3e4f5a6b7c";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("demo account env", () => {
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

  it("recognises only the demo email, ignoring case and spaces", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_EMAIL", "Demo@Example.com");
    vi.stubEnv("NEXT_PUBLIC_DEMO_PASSWORD", "public-demo");
    expect(isDemoEmail(" demo@example.COM ")).toBe(true);
    expect(isDemoEmail("owner@example.com")).toBe(false);
    expect(isDemoEmail(null)).toBe(false);
    expect(isDemoEmail(undefined)).toBe(false);
  });

  it("recognises nobody when no demo account is configured", () => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_EMAIL", "");
    vi.stubEnv("NEXT_PUBLIC_DEMO_PASSWORD", "");
    expect(isDemoEmail("")).toBe(false);
    expect(isDemoEmail("demo@example.com")).toBe(false);
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

  it("renders nothing unless enabled", () => {
    const { container } = render(<DemoBanner enabled={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("DemoAccountBanner", () => {
  it("shows while the demo account is signed in", async () => {
    sessionIsDemo.mockResolvedValue(true);
    render(await DemoAccountBanner());
    expect(screen.getByTestId("demo-banner")).toBeInTheDocument();
  });

  it("stays hidden for every other account", async () => {
    sessionIsDemo.mockResolvedValue(false);
    const { container } = render(await DemoAccountBanner());
    expect(container).toBeEmptyDOMElement();
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
  it("leaves simulated data out for a real account", () => {
    const params = parseAnalyticsParams({ vehicle: VEHICLE }, { demo: false });
    expect(params.includeSimulated).toBe(false);
    expect(apiQuery(params, VEHICLE).include_simulated).toBe(false);
    expect(analyticsSearch({ ...params, includeSimulated: true })).toContain(
      "simulated=1",
    );
    expect(analyticsSearch(params)).not.toContain("simulated");
  });

  it("includes simulated data for the demo account, and simulated=0 still leaves it out", () => {
    const demo = { demo: true };
    const params = parseAnalyticsParams({ vehicle: VEHICLE }, demo);
    expect(params.includeSimulated).toBe(true);
    expect(apiQuery(params, VEHICLE).include_simulated).toBe(true);
    expect(analyticsSearch(params, demo)).not.toContain("simulated");

    const realOnly = analyticsSearch({ ...params, includeSimulated: false }, demo);
    expect(realOnly).toContain("simulated=0");
    const back = Object.fromEntries(new URLSearchParams(realOnly.slice(1)));
    expect(parseAnalyticsParams(back, demo).includeSimulated).toBe(false);
  });
});
