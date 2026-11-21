import { fireEvent, render, screen } from "@testing-library/react";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthForm } from "@/components/auth/auth-form";
import {
  EXPIRED_AUTH_ERROR,
  GENERIC_AUTH_ERROR,
  authErrorMessage,
  signInErrorUrl,
} from "@/lib/auth-error";

let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search),
}));

const signInWithPassword = vi.fn();
const exchangeCodeForSession = vi.fn();
vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({ auth: { signInWithPassword } }),
}));
vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({ auth: { exchangeCodeForSession } }),
}));

const ORIGIN = "https://app.example";

describe("authErrorMessage", () => {
  it.each([
    ["error=invalid_request&error_code=bad_oauth_state&error_description=OAuth+state+has+expired", EXPIRED_AUTH_ERROR],
    ["error_code=flow_state_expired", EXPIRED_AUTH_ERROR],
    ["error=access_denied&error_code=otp_expired", EXPIRED_AUTH_ERROR],
    ["error=access_denied", GENERIC_AUTH_ERROR],
    ["error=server_error&error_code=unexpected_failure", GENERIC_AUTH_ERROR],
    ["error=x&error_code=__proto__", GENERIC_AUTH_ERROR],
    ["error=x&error_code=toString", GENERIC_AUTH_ERROR],
  ])("maps %s", (query, message) => {
    expect(authErrorMessage(new URLSearchParams(query))).toBe(message);
  });

  it.each(["", "next=/trips", "error=", "error_description=Something+bad"])(
    "returns null for %j",
    (query) => {
      expect(authErrorMessage(new URLSearchParams(query))).toBeNull();
    },
  );

  it("builds a sign-in URL that keeps only the error, code and next", () => {
    const url = signInErrorUrl(ORIGIN, { error: "invalid_request", code: "bad_oauth_state" }, "/trips");
    expect(url.toString()).toBe(
      `${ORIGIN}/sign-in?error=invalid_request&error_code=bad_oauth_state&next=%2Ftrips`,
    );
    expect(signInErrorUrl(ORIGIN, {}).toString()).toBe(`${ORIGIN}/sign-in?error=server_error`);
  });
});

describe("AuthForm URL errors", () => {
  beforeEach(() => {
    search = "";
    signInWithPassword.mockReset();
  });

  function renderForm(mode: "sign-in" | "sign-up" = "sign-in") {
    return render(<AuthForm mode={mode} googleEnabled={false} appleEnabled={false} />);
  }

  it("shows nothing without an error in the URL", () => {
    search = "next=/trips";
    renderForm();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains an expired OAuth state above the form", () => {
    search = "error=invalid_request&error_code=bad_oauth_state&error_description=OAuth+state+has+expired";
    const { container } = renderForm();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(EXPIRED_AUTH_ERROR);
    expect(alert).not.toHaveTextContent(/OAuth state/i);
    // Above the form, not under the buttons.
    expect(container.firstElementChild?.firstElementChild).toBe(alert);
  });

  it("falls back to a generic message and never renders the raw description", () => {
    search = `error=server_error&error_description=${encodeURIComponent("<img src=x onerror=alert(1)>")}`;
    const { container } = renderForm();
    expect(screen.getByRole("alert")).toHaveTextContent(GENERIC_AUTH_ERROR);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).not.toContain("onerror");
  });

  it("clears the message when the user tries again", async () => {
    search = "error=invalid_request&error_code=bad_oauth_state";
    signInWithPassword.mockResolvedValue({ error: null });
    renderForm();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "password123" } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);
    expect(screen.queryByText(EXPIRED_AUTH_ERROR)).not.toBeInTheDocument();
    await vi.waitFor(() => expect(signInWithPassword).toHaveBeenCalled());
  });

  it("doesn't show sign-in errors on the sign-up form", () => {
    search = "error=invalid_request&error_code=bad_oauth_state";
    renderForm("sign-up");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("/auth/callback", () => {
  beforeEach(() => exchangeCodeForSession.mockReset());

  async function get(query: string) {
    const { GET } = await import("@/app/auth/callback/route");
    const res = await GET(new NextRequest(`${ORIGIN}/auth/callback?${query}`));
    return new URL(res.headers.get("location")!);
  }

  it("forwards a Supabase error to the sign-in page", async () => {
    const to = await get(
      "next=%2Ftrips&error=invalid_request&error_code=bad_oauth_state&error_description=OAuth+state+has+expired",
    );
    expect(to.pathname).toBe("/sign-in");
    expect(to.searchParams.get("error")).toBe("invalid_request");
    expect(to.searchParams.get("error_code")).toBe("bad_oauth_state");
    expect(to.searchParams.get("error_description")).toBeNull();
    expect(to.searchParams.get("next")).toBe("/trips");
    expect(authErrorMessage(to.searchParams)).toBe(EXPIRED_AUTH_ERROR);
    expect(exchangeCodeForSession).not.toHaveBeenCalled();
  });

  it("sends a failed code exchange to the sign-in page with its code", async () => {
    exchangeCodeForSession.mockResolvedValue({
      error: { message: "invalid flow state, no valid flow state found", code: "flow_state_not_found" },
    });
    const to = await get("code=abc&next=%2Fgarage");
    expect(to.pathname).toBe("/sign-in");
    expect(to.searchParams.get("error")).toBe("exchange_failed");
    expect(to.searchParams.get("error_code")).toBe("flow_state_not_found");
    expect(authErrorMessage(to.searchParams)).toBe(EXPIRED_AUTH_ERROR);
  });

  it("gives a generic message when the exchange error has no code", async () => {
    exchangeCodeForSession.mockResolvedValue({ error: { message: "boom" } });
    const to = await get("code=abc");
    expect(to.searchParams.get("error_code")).toBeNull();
    expect(authErrorMessage(to.searchParams)).toBe(GENERIC_AUTH_ERROR);
  });

  it("still forwards a successful sign-in to next", async () => {
    exchangeCodeForSession.mockResolvedValue({ error: null });
    const to = await get("code=abc&next=%2Ftrips");
    expect(to.toString()).toBe(`${ORIGIN}/trips`);
  });
});
