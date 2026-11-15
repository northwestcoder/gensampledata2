// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import App from "./App";
import { LABELS } from "./types";
import type { Scenario } from "./types";

const scenario: Scenario = {
  schema_version: 1,
  profile: "legacy-retail",
  name: "retail-demo",
  people: 1000,
  seed: 42,
  reference_time: "2026-11-04T00:00:00Z",
  batch_size: 1000,
  sources: Object.keys(LABELS),
  formats: ["csv", "jsonl"],
  duplicate_fraction: 0.15,
  copies_per_duplicate: 1,
  overlap_fraction: 0.5,
  corruption_probability: 0.25,
  missing_probability: 0.02,
  max_transactions: 4,
  transaction_window: "full",
  points_percent: 2,
  tracer_file: null,
};
let calls: { path: string; body: unknown }[];
let saved: unknown[];
let jobs: unknown[];
let failSave = false;

beforeEach(() => {
  calls = [];
  saved = [];
  jobs = [];
  failSave = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(String(options.body)) : undefined;
      calls.push({ path, body });
      let data: unknown;
      if (path === "/api/meta")
        data = {
          version: "2.0.0",
          sources: Object.keys(LABELS),
          presets: { "retail-demo": scenario },
          schemas: Object.fromEntries(
            Object.keys(LABELS).map((key) => [key, ["id", "name"]]),
          ),
        };
      else if (path === "/api/scenarios" && options?.method === "POST") {
        if (failSave)
          return {
            ok: false,
            json: async () => ({
              detail: [{ loc: ["body", "name"], msg: "Invalid name" }],
            }),
          };
        saved = [{ name: body.name, version: 1, scenario: body }];
        data = { version: 1 };
      } else if (path === "/api/scenarios") data = saved;
      else if (path === "/api/preview")
        data = {
          truncated: false,
          records: [
            {
              source: "customers",
              record_id: "customer-1",
              entity_id: "E0000000001",
              duplicate: true,
              data: { name_first: "Jhon", email: "john@example.test" },
              original: { name_first: "John", email: "john@example.test" },
              mutations: [
                {
                  field: "name_first",
                  operation: "transpose",
                  before: "John",
                  after: "Jhon",
                },
              ],
            },
          ],
        };
      else if (path === "/api/jobs" && options?.method === "POST") {
        const job = {
          id: "run-1",
          status: "complete",
          scenario: body,
          records: 1150,
          counts: { customers: 1150 },
          error: null,
          created: "2026-11-04T00:00:00Z",
        };
        jobs = [job];
        data = job;
      } else if (path === "/api/jobs") data = jobs;
      else if (path === "/api/jobs/run-1/manifest")
        data = {
          counts: { customers: 1150 },
          duplicate_records: { customers: 150 },
          realized_duplicate_row_fraction: { customers: 150 / 1150 },
          total_records: 1150,
          elapsed_seconds: 0.5,
          verification: {
            valid: true,
            unique_entities: 1000,
            checks: ["schemas"],
          },
          corpus_sha256: "abc",
          files: {
            "customers.csv": { role: "dataset", bytes: 100, sha256: "abc" },
          },
        };
      else throw new Error(`Unexpected API request: ${path}`);
      return { ok: true, json: async () => data };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("workbench user flows", () => {
  it("edits controls, saves a version, and sends the edited shared scenario", async () => {
    render(<App />);
    await screen.findByLabelText(/^Base people/);
    fireEvent.change(screen.getByLabelText(/^Base people/), {
      target: { value: "1001" },
    });
    fireEvent.change(
      screen.getByRole("slider", { name: /Missing identifiers/ }),
      { target: { value: "50" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save version" }));
    await screen.findByText("Saved retail-demo, version 1.");
    const request = calls.find(
      (call) => call.path === "/api/scenarios" && call.body,
    )!.body as Scenario;
    expect(request.people).toBe(1001);
    expect(request.missing_probability).toBe(0.5);
  });
  it("shows the linked person and before/after changes in a preview", async () => {
    render(<App />);
    await screen.findByLabelText(/^Base people/);
    fireEvent.click(screen.getByRole("button", { name: "Preview records" }));
    await screen.findByRole("heading", {
      name: "See the person behind the records.",
    });
    expect(screen.getByText("John")).toBeTruthy();
    expect(screen.getByText("Jhon")).toBeTruthy();
    expect(screen.getByText("transpose")).toBeTruthy();
    expect(screen.getByText("ANSWER KEY · PREVIEW ONLY")).toBeTruthy();
  });
  it("starts a run and exposes verified results and downloads", async () => {
    render(<App />);
    await screen.findByLabelText(/^Base people/);
    fireEvent.click(
      screen.getAllByRole("button", { name: /Generate dataset/ })[0],
    );
    await screen.findByRole("link", { name: /customers.csv/ });
    expect(
      screen.getByRole("link", { name: /customers.csv/ }).getAttribute("href"),
    ).toBe("/api/jobs/run-1/download/customers.csv");
    expect(screen.getByText(/Passed 1 verification checks/)).toBeTruthy();
  });
  it("presents server validation errors and lets the user correct them", async () => {
    failSave = true;
    render(<App />);
    await screen.findByLabelText(/^Base people/);
    fireEvent.click(screen.getByRole("button", { name: "Save version" }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "name: Invalid name",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
