import { useEffect, useState } from "react";
import { errorText } from "./types";
import type { Scenario } from "./types";

export type CorpusSummary = {
  file_count: number;
  total_bytes: number;
  datasets: Record<string, { entries: number; bytes: number; usage: string }>;
};

export default function SupportData({
  catalog,
  targets,
  scenario,
  onChange,
}: {
  catalog: CorpusSummary;
  targets: string[];
  scenario: Scenario;
  onChange: (scenario: Scenario) => void;
}) {
  const [open, setOpen] = useState(false);
  const [dataset, setDataset] = useState("phones.csv");
  const [target, setTarget] = useState("wifi.device_uuid");
  const [sample, setSample] = useState<string[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError("");
    setSample([]);
    fetch(`/api/corpus/${encodeURIComponent(dataset)}?limit=5`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(errorText(data.detail));
        if (!controller.signal.aborted) setSample(data.sample);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [open, dataset]);
  const info = catalog.datasets[dataset];
  return (
    <details
      className="support-data"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        Original support data · {catalog.file_count} complete files
      </summary>
      <p>
        Every original dataset is available in full. Default generators use the
        original pools; use an override to assign another pool to an existing
        output field.
      </p>
      <div className="field-grid">
        <label>
          Support dataset
          <select
            value={dataset}
            onChange={(event) => setDataset(event.target.value)}
          >
            {Object.keys(catalog.datasets).map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Output field
          <select
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          >
            {targets.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
      </div>
      <p>
        {info.entries.toLocaleString()} entries · {info.usage}. Original
        ordering and duplicates are retained.
      </p>
      {error && <p role="alert">{error}</p>}
      {sample.length > 0 && (
        <pre className="support-sample" aria-label="Support data sample">
          {sample.join("\n")}
        </pre>
      )}
      <button
        type="button"
        onClick={() =>
          onChange({
            ...scenario,
            field_pools: { ...scenario.field_pools, [target]: dataset },
          })
        }
      >
        Use this pool for this field
      </button>
      <p>
        Overrides use stored strings, including original historical dates.
        Identity and foreign-key fields cannot be overridden.
      </p>
      {Object.entries(scenario.field_pools || {}).map(([field, pool]) => (
        <div className="pool-binding" key={field}>
          <span>
            {field} ← {pool}
          </span>
          <button
            type="button"
            aria-label={`Remove ${field} override`}
            onClick={() => {
              const next = { ...scenario.field_pools };
              delete next[field];
              onChange({ ...scenario, field_pools: next });
            }}
          >
            Remove
          </button>
        </div>
      ))}
    </details>
  );
}
