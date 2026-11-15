// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import SupportData from "./SupportData";
import type { Scenario } from "./types";

const scenario: Scenario = {
  schema_version: 1,
  profile: "legacy-retail",
  name: "example",
  people: 1,
  seed: 42,
  reference_time: "2026-11-04T00:00:00Z",
  batch_size: 1000,
  sources: ["wifi"],
  formats: ["csv"],
  duplicate_fraction: 0,
  copies_per_duplicate: 1,
  overlap_fraction: 0.5,
  corruption_probability: 0,
  missing_probability: 0,
  max_transactions: 1,
  transaction_window: "daily",
  points_percent: 2,
  tracer_file: null,
};
const catalog = {
  file_count: 36,
  total_bytes: 16961300,
  datasets: {
    "phones.csv": { entries: 300000, bytes: 1, usage: "Customer phones" },
    "macaddr.csv": { entries: 1000, bytes: 1, usage: "Device addresses" },
  },
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("lets a user inspect an original pool and bind it to a supported field", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ sample: ["202-555-0101"] }),
    })),
  );
  const change = vi.fn();
  render(
    <SupportData
      catalog={catalog}
      targets={["wifi.device_uuid"]}
      scenario={scenario}
      onChange={change}
    />,
  );
  fireEvent.click(screen.getByText(/Original support data/));
  await screen.findByText("202-555-0101");
  fireEvent.change(screen.getByLabelText("Support dataset"), {
    target: { value: "macaddr.csv" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Use this pool for this field" }),
  );
  expect(change.mock.calls.at(-1)?.[0].field_pools).toEqual({
    "wifi.device_uuid": "macaddr.csv",
  });
});

it("removes a field override without changing the rest of the scenario", () => {
  const change = vi.fn();
  render(
    <SupportData
      catalog={catalog}
      targets={["wifi.device_uuid"]}
      scenario={{ ...scenario, field_pools: { "wifi.device_uuid": "macaddr" } }}
      onChange={change}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Remove wifi.device_uuid override" }),
  );
  expect(change.mock.calls[0][0]).toEqual({ ...scenario, field_pools: {} });
});
