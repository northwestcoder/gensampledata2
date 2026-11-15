import { describe, expect, it } from "vitest";
import { errorText, toggleSource } from "./types";
describe("source dependencies", () => {
  it("adds parents when enabling orders", () => {
    expect(toggleSource([], "transactions")).toEqual([
      "customers",
      "transactions",
    ]);
    expect(toggleSource([], "pos_transactions")).toEqual([
      "pos",
      "pos_transactions",
    ]);
  });
  it("removes dependent orders when removing a customer source", () => {
    expect(
      toggleSource(["customers", "transactions", "wifi"], "customers"),
    ).toEqual(["wifi"]);
  });
  it("presents validation errors with field names", () => {
    expect(
      errorText([{ loc: ["body", "people"], msg: "Must be positive" }]),
    ).toBe("people: Must be positive");
  });
});
