import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FavoriteToggle } from "@/components/stations/favorite-toggle";

const setStationFavorite = vi.fn();
vi.mock("@/components/stations/favorite-actions", () => ({
  setStationFavorite: (id: string, favorite: boolean) => setStationFavorite(id, favorite),
}));

const ID = "8f0c7f8e-2b8e-4a55-9c1b-7d1a0e6f3c21";

describe("FavoriteToggle", () => {
  beforeEach(() => setStationFavorite.mockReset());

  it("stars a station and reports the change", async () => {
    setStationFavorite.mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(<FavoriteToggle stationId={ID} stationName="Kehne DC" favorite={false} onChange={onChange} />);
    const button = screen.getByRole("button", { name: "Add Kehne DC to favorites" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(button);
    // Flips at once, before the save finishes.
    expect(screen.getByRole("button", { name: "Remove Kehne DC from favorites" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(true));
    expect(setStationFavorite).toHaveBeenCalledWith(ID, true);
  });

  it("unstars a favorite", async () => {
    setStationFavorite.mockResolvedValue(undefined);
    render(<FavoriteToggle stationId={ID} stationName="Kehne DC" favorite />);
    fireEvent.click(screen.getByRole("button", { name: "Remove Kehne DC from favorites" }));
    await waitFor(() => expect(setStationFavorite).toHaveBeenCalledWith(ID, false));
    expect(screen.getByRole("button", { name: "Add Kehne DC to favorites" })).toBeInTheDocument();
  });

  it("flips back and shows why when saving fails", async () => {
    setStationFavorite.mockResolvedValue({ error: "Couldn't add this station to favorites." });
    const onChange = vi.fn();
    render(<FavoriteToggle stationId={ID} stationName="Kehne DC" favorite={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Add Kehne DC to favorites" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Couldn't add this station to favorites."),
    );
    expect(screen.getByRole("button", { name: "Add Kehne DC to favorites" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  it("doesn't pass the click on to the card around it", async () => {
    setStationFavorite.mockResolvedValue(undefined);
    const onCardClick = vi.fn();
    render(
      <div onClick={onCardClick}>
        <FavoriteToggle stationId={ID} stationName="Kehne DC" favorite={false} />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add Kehne DC to favorites" }));
    await waitFor(() => expect(setStationFavorite).toHaveBeenCalled());
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it("draws the star in a valid 20x20 viewBox", () => {
    render(<FavoriteToggle stationId={ID} stationName="Kehne DC" favorite={false} />);
    const svg = screen.getByRole("button", { name: "Add Kehne DC to favorites" }).querySelector("svg");
    expect(svg).toHaveAttribute("viewBox", "0 0 20 20");
  });

  it("follows a new value from the server", () => {
    const { rerender } = render(<FavoriteToggle stationId={ID} stationName="Kehne DC" favorite={false} />);
    rerender(<FavoriteToggle stationId={ID} stationName="Kehne DC" favorite />);
    expect(screen.getByRole("button", { name: "Remove Kehne DC from favorites" })).toBeInTheDocument();
  });
});
