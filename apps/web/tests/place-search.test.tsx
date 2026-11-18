import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlaceSearch, SUGGEST_DEBOUNCE_MS } from "@/components/places/place-search";

const suggestPlaces = vi.fn();
const retrievePlace = vi.fn();
vi.mock("@/components/places/actions", () => ({
  suggestPlaces: (input: unknown) => suggestPlaces(input),
  retrievePlace: (input: unknown) => retrievePlace(input),
}));

const TORONTO = {
  id: "dXJuOm1ieHBsYzpCK3dJSnc",
  name: "Toronto",
  full_address: "Toronto, Ontario, Canada",
  kind: "place" as const,
  country_code: "CA",
};
const TORONTO_JUNCTION = { ...TORONTO, id: "junction", name: "Toronto Junction" };

function setup(props: Partial<Parameters<typeof PlaceSearch>[0]> = {}) {
  const onSelect = vi.fn();
  render(<PlaceSearch label="Search near" onSelect={onSelect} {...props} />);
  const input = screen.getByRole("combobox", { name: "Search near" });
  return { input, onSelect };
}

async function type(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

async function flushDebounce() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(SUGGEST_DEBOUNCE_MS);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  suggestPlaces.mockResolvedValue({ items: [TORONTO, TORONTO_JUNCTION] });
  retrievePlace.mockResolvedValue({
    place: { ...TORONTO, location: { lat: 43.647938, lon: -79.38355 } },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("PlaceSearch", () => {
  it("waits for 3 characters and debounces keystrokes into one request", async () => {
    const { input } = setup({ near: { lat: 39.41, lon: -77.41 } });
    await type(input, "To");
    await flushDebounce();
    expect(suggestPlaces).not.toHaveBeenCalled();

    await type(input, "Tor");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SUGGEST_DEBOUNCE_MS - 50);
    });
    await type(input, "Toro");
    await type(input, "Toron");
    await flushDebounce();

    expect(suggestPlaces).toHaveBeenCalledTimes(1);
    expect(suggestPlaces).toHaveBeenCalledWith(
      expect.objectContaining({ q: "Toron", near: { lat: 39.41, lon: -77.41 } }),
    );
    expect(screen.getByRole("option", { name: /Toronto Junction/ })).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-expanded", "true");
  });

  it("selects with the keyboard via aria-activedescendant", async () => {
    const { input, onSelect } = setup();
    await type(input, "Toronto");
    await flushDebounce();

    const options = screen.getAllByRole("option");
    expect(input).toHaveAttribute("aria-activedescendant", options[0].id);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveAttribute("aria-activedescendant", options[1].id);
    expect(options[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowDown" }); // wraps
    expect(input).toHaveAttribute("aria-activedescendant", options[0].id);
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input).toHaveAttribute("aria-activedescendant", options[1].id);

    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    expect(retrievePlace).toHaveBeenCalledWith(expect.objectContaining({ id: "junction" }));
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ location: { lat: 43.647938, lon: -79.38355 } }),
    );
    expect(input).toHaveValue("Toronto");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on Escape", async () => {
    const { input } = setup();
    await type(input, "Toronto");
    await flushDebounce();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).not.toHaveAttribute("aria-activedescendant");
  });

  it("uses one session token per search session and a new one after a pick", async () => {
    const { input } = setup();
    await type(input, "Tor");
    await flushDebounce();
    await type(input, "Toronto");
    await flushDebounce();
    await act(async () => {
      fireEvent.mouseDown(screen.getAllByRole("option")[0]);
    });

    const tokens = [
      ...suggestPlaces.mock.calls.map(([arg]) => arg.sessionToken),
      ...retrievePlace.mock.calls.map(([arg]) => arg.sessionToken),
    ];
    expect(tokens).toHaveLength(3);
    expect(new Set(tokens).size).toBe(1);
    expect(tokens[0]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);

    await type(input, "Frederick");
    await flushDebounce();
    const next = suggestPlaces.mock.calls.at(-1)?.[0].sessionToken;
    expect(next).not.toBe(tokens[0]);
  });

  it("ignores a slow response that a newer query replaced", async () => {
    let resolveFirst: (v: unknown) => void = () => {};
    suggestPlaces.mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)));
    const { input } = setup();
    await type(input, "Tor");
    await flushDebounce();
    await type(input, "Frederick");
    suggestPlaces.mockResolvedValueOnce({
      items: [{ ...TORONTO, id: "fred", name: "Frederick" }],
    });
    await flushDebounce();
    await act(async () => resolveFirst({ items: [TORONTO] }));

    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      expect.stringContaining("Frederick"),
    ]);
  });

  it("shows an error state when search is unavailable", async () => {
    suggestPlaces.mockResolvedValueOnce({ error: "unavailable" });
    const { input } = setup();
    await type(input, "Toronto");
    await flushDebounce();
    expect(screen.getByRole("alert")).toHaveTextContent("Place search isn't available");
  });

  it("shows an error when the picked place can't be resolved", async () => {
    retrievePlace.mockResolvedValueOnce({ error: "not_found" });
    const { input, onSelect } = setup();
    await type(input, "Toronto");
    await flushDebounce();
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("couldn't be found");
  });

  it("says when nothing matched", async () => {
    suggestPlaces.mockResolvedValueOnce({ items: [] });
    const { input } = setup();
    await type(input, "zzzzzz");
    await flushDebounce();
    expect(screen.getByText("No places found.")).toBeInTheDocument();
  });
});
