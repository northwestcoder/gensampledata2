import { useEffect, useRef, useState } from "react";
import anvilLogo from "../../anvil.png";
import { ACTIVE, LABELS, errorText, toggleSource } from "./types";
import type { Scenario, SyntheticRecord, Job, Manifest } from "./types";
import SupportData from "./SupportData";
import type { CorpusSummary } from "./SupportData";
import "./styles.css";

type Meta = {
  version: string;
  sources: string[];
  presets: Record<string, Scenario>;
  schemas: Record<string, string[]>;
  corpus?: CorpusSummary;
  field_pool_targets?: string[];
};
type Saved = { name: string; version: number; scenario: Scenario };
const fmt = (value: number) => value.toLocaleString();
const bytes = (value: number) =>
  value > 1048576
    ? `${(value / 1048576).toFixed(1)} MB`
    : `${(value / 1024).toFixed(1)} KB`;

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json();
  if (!response.ok) throw new Error(errorText(data.detail));
  return data as T;
}

export default function App() {
  const [meta, setMeta] = useState<Meta>();
  const [scenario, setScenario] = useState<Scenario>();
  const [saved, setSaved] = useState<Saved[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tab, setTab] = useState<"configure" | "preview" | "runs">("configure");
  const [records, setRecords] = useState<SyntheticRecord[]>([]);
  const [previewScenario, setPreviewScenario] = useState<Scenario>();
  const [selected, setSelected] = useState<SyntheticRecord>();
  const [source, setSource] = useState("all");
  const [entity, setEntity] = useState("all");
  const [selectedJob, setSelectedJob] = useState<string>();
  const [manifest, setManifest] = useState<Manifest>();
  const [sample, setSample] = useState<Record<string, unknown>[]>([]);
  const [sampleSource, setSampleSource] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("gensampledata");
  const [includeTruth, setIncludeTruth] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  const active = jobs.some((job) => ACTIVE.has(job.status));
  const job = jobs.find((item) => item.id === selectedJob);

  useEffect(() => {
    let current = true;
    Promise.all([
      api<Meta>("/meta"),
      api<Saved[]>("/scenarios"),
      api<Job[]>("/jobs"),
    ])
      .then(([info, stored, history]) => {
        if (current) {
          setMeta(info);
          setScenario(info.presets["retail-demo"]);
          setSaved(stored);
          setJobs(history);
        }
      })
      .catch((cause) => {
        if (current) setError(cause.message);
      });
    return () => {
      current = false;
    };
  }, []);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(
      () =>
        api<Job[]>("/jobs")
          .then(setJobs)
          .catch((cause) => setError(cause.message)),
      1500,
    );
    return () => clearInterval(timer);
  }, [active]);
  useEffect(() => {
    let current = true;
    setManifest(undefined);
    setSample([]);
    setSampleSource("");
    if (selectedJob && job?.status === "complete")
      api<Manifest>(`/jobs/${selectedJob}/manifest`)
        .then((value) => {
          if (current) setManifest(value);
        })
        .catch((cause) => {
          if (current) setError(cause.message);
        });
    return () => {
      current = false;
    };
  }, [selectedJob, job?.status]);

  function update<K extends keyof Scenario>(key: K, value: Scenario[K]) {
    setScenario((current) =>
      current ? { ...current, [key]: value } : current,
    );
    setNotice("");
  }
  async function task(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }
  function choose(config: Scenario) {
    setScenario({ ...config });
    setTab("configure");
    setNotice(`Loaded ${config.name}`);
    setError("");
  }
  async function refreshJobs() {
    setJobs(await api<Job[]>("/jobs"));
  }
  function generate() {
    if (!scenario) return;
    void task("Starting run", async () => {
      const created = await api<Job>("/jobs", scenario);
      setSelectedJob(created.id);
      setTab("runs");
      await refreshJobs();
    });
  }
  function inspect() {
    if (!scenario) return;
    void task("Building preview", async () => {
      const result = await api<{
        records: SyntheticRecord[];
        truncated: boolean;
      }>("/preview", { scenario, people: 5 });
      setRecords(result.records);
      setPreviewScenario({ ...scenario });
      setSelected(result.records[0]);
      setSource("all");
      setEntity("all");
      setTab("preview");
      setNotice(
        result.truncated
          ? "Showing the first 500 records from the first five people."
          : "Preview of the first five people. The same records appear in a full run.",
      );
    });
  }
  async function exportConfig() {
    const response = await fetch("/api/scenarios/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scenario),
    });
    if (!response.ok)
      throw new Error(errorText((await response.json()).detail));
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `${scenario?.name}.toml`;
    link.click();
    URL.revokeObjectURL(url);
  }
  const visible = records.filter(
    (record) =>
      (source === "all" || record.source === source) &&
      (entity === "all" || record.entity_id === entity),
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="gensampledata home">
          <img
            className="brand-mark"
            src={anvilLogo}
            alt="Original anvil logo"
          />
          <span>
            gensampledata<small>DATA WORKBENCH</small>
          </span>
        </a>
        <div className="sidebar-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          <button
            className={tab === "configure" ? "nav active" : "nav"}
            onClick={() => setTab("configure")}
          >
            <span>01</span> Configure scenario
          </button>
          <button
            className={tab === "preview" ? "nav active" : "nav"}
            onClick={() => setTab("preview")}
          >
            <span>02</span> Inspect records
          </button>
          <button
            className={tab === "runs" ? "nav active" : "nav"}
            onClick={() => setTab("runs")}
          >
            <span>03</span> Run history <b>{jobs.length}</b>
          </button>
        </nav>
        <div className="sidebar-label presets-label">START FROM A PRESET</div>
        {meta &&
          Object.entries(meta.presets).map(([key, config]) => (
            <button key={key} className="preset" onClick={() => choose(config)}>
              {key.replaceAll("-", " ")}
              <span>↗</span>
            </button>
          ))}
        <div className="sidebar-bottom">
          <span className="online-dot" /> Local workspace
          <small>Engine {meta?.version || "…"} · UTC timestamps</small>
        </div>
      </aside>
      <main>
        <div className="topline">
          <span>SYNTHETIC DATA / {tab.toUpperCase()}</span>
          <span className="quiet">Reproducible by design</span>
        </div>
        <header className="page-heading">
          <div>
            <p className="eyebrow">
              RELATED RECORDS. CONTROLLED IMPERFECTIONS.
            </p>
            <h1>
              {tab === "configure"
                ? "gensampledata"
                : tab === "preview"
                  ? "See the person behind the records."
                  : "Every run, accounted for."}
            </h1>
            <p className="subtitle">
              {tab === "configure"
                ? "Build realistic customer ecosystems, then choose how messy they get."
                : tab === "preview"
                  ? "Follow identities across sources and inspect exactly what changed."
                  : "Track generation, inspect validated exports, and reproduce the result."}
            </p>
          </div>
          <div className="heading-actions">
            <button disabled={!!busy || !scenario} onClick={inspect}>
              Preview records
            </button>
            <button
              className="primary"
              disabled={!!busy || !scenario}
              onClick={generate}
            >
              Generate dataset <span>↗</span>
            </button>
          </div>
        </header>
        {error && (
          <div className="message error" role="alert">
            {error}
            <button onClick={() => setError("")} aria-label="Dismiss error">
              ×
            </button>
          </div>
        )}
        {notice && (
          <div className="message" role="status">
            {notice}
          </div>
        )}
        {busy && (
          <div className="working" role="status">
            <span className="spinner" />
            {busy}…
          </div>
        )}
        {!scenario || !meta ? (
          <div className="loading">
            {error
              ? "The workbench could not connect to its local API. Restart the server and reload."
              : "Loading your workspace…"}
          </div>
        ) : (
          <>
            {tab === "configure" && (
              <>
                <div className="summary-strip">
                  <div>
                    <span>BASE PEOPLE</span>
                    <strong>{fmt(scenario.people)}</strong>
                  </div>
                  <div>
                    <span>CONNECTED SOURCES</span>
                    <strong>
                      {scenario.sources.length.toString().padStart(2, "0")}
                    </strong>
                  </div>
                  <div>
                    <span>PEOPLE WITH COPIES</span>
                    <strong>
                      {Math.round(scenario.duplicate_fraction * 100)}
                      <small>%</small>
                    </strong>
                  </div>
                  <div>
                    <span>RANDOM SEED</span>
                    <strong className="mono">{scenario.seed}</strong>
                  </div>
                </div>
                <div className="editor-layout">
                  <div className="editor">
                    <section className="panel">
                      <div className="section-title">
                        <span className="section-index">01</span>
                        <div>
                          <h2>Scenario essentials</h2>
                          <p>
                            A saved configuration works in the visual editor and
                            the CLI.
                          </p>
                        </div>
                      </div>
                      <div className="field-grid">
                        <label>
                          Scenario name
                          <input
                            value={scenario.name}
                            maxLength={80}
                            onChange={(e) => update("name", e.target.value)}
                          />
                        </label>
                        <label>
                          Base people
                          <input
                            type="number"
                            min={0}
                            max={1000000}
                            value={scenario.people}
                            onChange={(e) =>
                              update("people", Number(e.target.value))
                            }
                          />
                          <small>Unique people in the primary population</small>
                        </label>
                        <label>
                          Random seed
                          <input
                            type="number"
                            min={0}
                            value={scenario.seed}
                            onChange={(e) =>
                              update("seed", Number(e.target.value))
                            }
                          />
                        </label>
                        <label>
                          Reference time (UTC)
                          <input
                            type="datetime-local"
                            value={scenario.reference_time.slice(0, 16)}
                            onChange={(e) =>
                              update("reference_time", `${e.target.value}:00Z`)
                            }
                          />
                        </label>
                      </div>
                      <div className="toolbar">
                        <button
                          disabled={!!busy}
                          onClick={() =>
                            void task("Saving scenario", async () => {
                              const value = await api<{ version: number }>(
                                "/scenarios",
                                scenario,
                              );
                              setSaved(await api<Saved[]>("/scenarios"));
                              setNotice(
                                `Saved ${scenario.name}, version ${value.version}.`,
                              );
                            })
                          }
                        >
                          Save version
                        </button>
                        <button
                          disabled={!!busy}
                          onClick={() =>
                            void task("Exporting scenario", exportConfig)
                          }
                        >
                          Export TOML
                        </button>
                        <button onClick={() => upload.current?.click()}>
                          Import scenario
                        </button>
                        <input
                          ref={upload}
                          type="file"
                          accept=".toml,.json"
                          hidden
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file)
                              void task("Importing scenario", async () => {
                                const config = await api<Scenario>(
                                  "/scenarios/import",
                                  {
                                    text: await file.text(),
                                    format: file.name.endsWith(".json")
                                      ? "json"
                                      : "toml",
                                  },
                                );
                                choose(config);
                              });
                            e.target.value = "";
                          }}
                        />
                      </div>
                    </section>
                    <section className="panel">
                      <div className="section-title">
                        <span className="section-index">02</span>
                        <div>
                          <h2>Choose your sources</h2>
                          <p>
                            Related systems share people, with their own records
                            and identifiers.
                          </p>
                        </div>
                      </div>
                      <div className="source-grid">
                        {meta.sources.map((key) => (
                          <label
                            key={key}
                            className={`source-option ${scenario.sources.includes(key) ? "checked" : ""}`}
                          >
                            <input
                              type="checkbox"
                              checked={scenario.sources.includes(key)}
                              onChange={() =>
                                update(
                                  "sources",
                                  toggleSource(scenario.sources, key),
                                )
                              }
                            />
                            <span>
                              {LABELS[key]}
                              <small>{meta.schemas[key].length} fields</small>
                            </span>
                          </label>
                        ))}
                      </div>
                    </section>
                    <section className="panel">
                      <div className="section-title">
                        <span className="section-index">03</span>
                        <div>
                          <h2>Dial in the imperfections</h2>
                          <p>
                            Control duplication, overlap, and data quality
                            independently.
                          </p>
                        </div>
                      </div>
                      {(
                        [
                          [
                            "duplicate_fraction",
                            "People with duplicate records",
                            "Fraction of base people receiving additional source records.",
                          ],
                          [
                            "overlap_fraction",
                            "POS overlap with primary people",
                            "The rest of the POS population consists of different people.",
                          ],
                          [
                            "corruption_probability",
                            "Corruption on duplicate fields",
                            "Chance of a typo, alias, formatting change, or abbreviation.",
                          ],
                          [
                            "missing_probability",
                            "Missing identifiers",
                            "Chance of an empty value in supported name, email, address, or phone fields.",
                          ],
                        ] as const
                      ).map(([key, label, help]) => (
                        <label className="range-field" key={key}>
                          <span>
                            {label}
                            <strong>{Math.round(scenario[key] * 100)}%</strong>
                          </span>
                          <input
                            type="range"
                            min={0}
                            max={100}
                            value={Math.round(scenario[key] * 100)}
                            onChange={(e) =>
                              update(key, Number(e.target.value) / 100)
                            }
                          />
                          <small>{help}</small>
                        </label>
                      ))}
                      <div className="field-grid">
                        <label>
                          Extra copies per selected person
                          <input
                            type="number"
                            min={1}
                            max={10}
                            value={scenario.copies_per_duplicate}
                            onChange={(e) =>
                              update(
                                "copies_per_duplicate",
                                Number(e.target.value),
                              )
                            }
                          />
                        </label>
                        <label>
                          Maximum orders per customer record
                          <input
                            type="number"
                            min={1}
                            max={100}
                            value={scenario.max_transactions}
                            onChange={(e) =>
                              update("max_transactions", Number(e.target.value))
                            }
                          />
                        </label>
                      </div>
                    </section>
                    <section className="panel">
                      <div className="section-title">
                        <span className="section-index">04</span>
                        <div>
                          <h2>Output & activity</h2>
                          <p>All selected formats contain the same records.</p>
                        </div>
                      </div>
                      <div className="field-grid">
                        <label>
                          Transaction window
                          <select
                            value={scenario.transaction_window}
                            onChange={(e) =>
                              update("transaction_window", e.target.value)
                            }
                          >
                            <option value="full">
                              Five years before reference time
                            </option>
                            <option value="daily">
                              24 hours before reference time
                            </option>
                          </select>
                        </label>
                        <label>
                          Timestamp interpretation
                          <select
                            value={scenario.date_mode || "relative"}
                            onChange={(event) =>
                              update(
                                "date_mode",
                                event.target.value as "relative" | "original",
                              )
                            }
                          >
                            <option value="relative">
                              Rebase original timestamp samples to reference
                              time
                            </option>
                            <option value="original">
                              Use original historical timestamps unchanged
                            </option>
                          </select>
                        </label>
                        <label>
                          Batch size
                          <input
                            type="number"
                            min={1}
                            max={100000}
                            value={scenario.batch_size}
                            onChange={(e) =>
                              update("batch_size", Number(e.target.value))
                            }
                          />
                          <small>
                            Changes processing batches, not record identities
                          </small>
                        </label>
                        <label>
                          Points earned (%)
                          <input
                            type="number"
                            min={0}
                            max={100}
                            value={scenario.points_percent}
                            onChange={(e) =>
                              update("points_percent", Number(e.target.value))
                            }
                          />
                        </label>
                      </div>
                      <div className="format-options">
                        {["csv", "jsonl"].map((format) => (
                          <label key={format}>
                            <input
                              type="checkbox"
                              checked={scenario.formats.includes(format)}
                              onChange={() =>
                                update(
                                  "formats",
                                  scenario.formats.includes(format)
                                    ? scenario.formats.filter(
                                        (item) => item !== format,
                                      )
                                    : [...scenario.formats, format],
                                )
                              }
                            />
                            {format.toUpperCase()}
                          </label>
                        ))}
                      </div>
                      {meta.corpus && (
                        <SupportData
                          catalog={meta.corpus}
                          targets={meta.field_pool_targets || []}
                          scenario={scenario}
                          onChange={setScenario}
                        />
                      )}
                    </section>
                  </div>
                  <aside className="right-rail">
                    <div className="run-plan">
                      <span className="eyebrow">YOUR RUN, AT A GLANCE</span>
                      <h2>{scenario.name || "Untitled scenario"}</h2>
                      <dl>
                        <div>
                          <dt>Primary people</dt>
                          <dd>{fmt(scenario.people)}</dd>
                        </div>
                        <div>
                          <dt>Extra customer copies</dt>
                          <dd>
                            {fmt(
                              Math.round(
                                scenario.people * scenario.duplicate_fraction,
                              ) * scenario.copies_per_duplicate,
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>Shared POS people</dt>
                          <dd>
                            {scenario.sources.includes("pos")
                              ? fmt(
                                  Math.round(
                                    scenario.people * scenario.overlap_fraction,
                                  ),
                                )
                              : "—"}
                          </dd>
                        </div>
                        <div>
                          <dt>Export formats</dt>
                          <dd>
                            {scenario.formats.join(" + ").toUpperCase() ||
                              "Choose one"}
                          </dd>
                        </div>
                      </dl>
                      <p>
                        Each completed run includes an answer key, mutation
                        history, a configuration snapshot, and file checksums.
                      </p>
                      <button
                        className="primary full-width"
                        disabled={!!busy}
                        onClick={generate}
                      >
                        Generate dataset ↗
                      </button>
                      <small>
                        Existing runs are preserved. New runs get their own
                        directory.
                      </small>
                    </div>
                    <div className="rail-note">
                      <h3>A person is not a record.</h3>
                      <p>
                        Copies and overlapping sources can describe the same
                        person. The separate answer key tracks those connections
                        without revealing them in your test inputs.
                      </p>
                    </div>
                    {saved.length > 0 && (
                      <div className="saved">
                        <h3>Saved scenarios</h3>
                        {saved.map((item) => (
                          <div key={item.name} className="saved-row">
                            <button onClick={() => choose(item.scenario)}>
                              {item.name}
                              <small>Version {item.version}</small>
                            </button>
                            <button
                              aria-label={`Clone ${item.name}`}
                              onClick={() =>
                                choose({
                                  ...item.scenario,
                                  name: `${item.name.slice(0, 74)}-copy`,
                                })
                              }
                            >
                              Clone
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </aside>
                </div>
              </>
            )}
            {tab === "preview" && (
              <>
                {!previewScenario ? (
                  <section className="empty">
                    <span className="section-index">02</span>
                    <h2>Meet your first five people.</h2>
                    <p>
                      Preview the current scenario to inspect related records
                      and their transformations.
                    </p>
                    <button
                      className="primary"
                      onClick={inspect}
                      disabled={!!busy}
                    >
                      Build preview
                    </button>
                  </section>
                ) : (
                  <>
                    <div className="section-bar">
                      <div>
                        <h2>{previewScenario.name}</h2>
                        <span className="quiet">
                          Seed {previewScenario.seed} · {records.length} preview
                          records
                        </span>
                      </div>
                      <div className="filters">
                        <label>
                          Source
                          <select
                            value={source}
                            onChange={(e) => {
                              setSource(e.target.value);
                              setSelected(undefined);
                            }}
                          >
                            <option value="all">All sources</option>
                            {previewScenario.sources.map((key) => (
                              <option key={key} value={key}>
                                {LABELS[key]}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Person
                          <select
                            value={entity}
                            onChange={(e) => {
                              setEntity(e.target.value);
                              setSelected(undefined);
                            }}
                          >
                            <option value="all">All people</option>
                            {[
                              ...new Set(
                                records.map((record) => record.entity_id),
                              ),
                            ].map((key) => (
                              <option key={key}>{key}</option>
                            ))}
                          </select>
                        </label>
                      </div>
                    </div>
                    <div className="preview-layout">
                      <div className="table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>Source / record</th>
                              <th>Person</th>
                              <th>Changes</th>
                            </tr>
                          </thead>
                          <tbody>
                            {visible.map((record) => (
                              <tr
                                key={`${record.source}:${record.record_id}`}
                                className={
                                  selected === record ? "selected" : ""
                                }
                              >
                                <td>
                                  <button
                                    className="record-link"
                                    onClick={() => setSelected(record)}
                                  >
                                    {LABELS[record.source]}
                                    <small>{record.record_id}</small>
                                  </button>
                                </td>
                                <td>
                                  <span className="mono">
                                    {record.entity_id}
                                  </span>
                                  {record.duplicate && (
                                    <span className="badge">copy</span>
                                  )}
                                </td>
                                <td>
                                  {record.mutations.length ? (
                                    <span className="changed-count">
                                      {record.mutations.length} fields
                                    </span>
                                  ) : (
                                    <span className="quiet">Original</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {visible.length === 0 && (
                          <p className="empty">
                            No records match these filters. Choose another
                            source or person.
                          </p>
                        )}
                      </div>
                      <aside className="inspector">
                        {selected ? (
                          <>
                            <span className="eyebrow">RECORD INSPECTOR</span>
                            <h2>{LABELS[selected.source]}</h2>
                            <p className="mono record-id">
                              {selected.record_id}
                            </p>
                            <div className="identity">
                              <small>ANSWER KEY · PREVIEW ONLY</small>
                              <b>{selected.entity_id}</b>
                            </div>
                            <p className="quiet">
                              Highlighted values differ from the clean source
                              projection.
                            </p>
                            <div className="table-wrap">
                              <table className="field-table">
                                <thead>
                                  <tr>
                                    <th>Field</th>
                                    <th>Original</th>
                                    <th>Output</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {Object.entries(selected.data).map(
                                    ([field, value]) => {
                                      const mutation = selected.mutations.find(
                                        (item) => item.field === field,
                                      );
                                      return (
                                        <tr key={field}>
                                          <th>{field}</th>
                                          <td>
                                            {String(
                                              selected.original[field] ?? "∅",
                                            )}
                                          </td>
                                          <td>
                                            {mutation ? (
                                              <mark title={mutation.operation}>
                                                {String(value) || "∅"}
                                                <small>
                                                  {mutation.operation.replaceAll(
                                                    "_",
                                                    " ",
                                                  )}
                                                </small>
                                              </mark>
                                            ) : (
                                              String(value ?? "∅")
                                            )}
                                          </td>
                                        </tr>
                                      );
                                    },
                                  )}
                                </tbody>
                              </table>
                            </div>
                          </>
                        ) : (
                          <p>
                            Choose a record to inspect its fields and changes.
                          </p>
                        )}
                      </aside>
                    </div>
                  </>
                )}
              </>
            )}
            {tab === "runs" && (
              <div className="runs-layout">
                <section>
                  <div className="section-bar">
                    <h2>Recent runs</h2>
                    <button
                      onClick={() => void task("Refreshing runs", refreshJobs)}
                      disabled={!!busy}
                    >
                      Refresh
                    </button>
                  </div>
                  {jobs.length === 0 ? (
                    <div className="empty">
                      <h3>Your first run starts here.</h3>
                      <p>
                        Generate a dataset to see its progress and verified
                        exports.
                      </p>
                    </div>
                  ) : (
                    jobs.map((item) => (
                      <button
                        className={`run-row ${item.id === selectedJob ? "selected" : ""}`}
                        key={item.id}
                        onClick={() => setSelectedJob(item.id)}
                      >
                        <div>
                          <b>{item.scenario.name}</b>
                          <small>
                            {new Date(item.created).toLocaleString()} · Seed{" "}
                            {item.scenario.seed}
                          </small>
                        </div>
                        <span className={`status status-${item.status}`}>
                          {item.status}
                        </span>
                        <strong>
                          {fmt(item.records)}
                          <small>records</small>
                        </strong>
                      </button>
                    ))
                  )}
                </section>
                <section className="run-detail">
                  {!job ? (
                    <div className="empty">
                      Choose a run to inspect its results.
                    </div>
                  ) : (
                    <>
                      <span className="eyebrow">RUN {job.id.slice(0, 8)}</span>
                      <h2>{job.scenario.name}</h2>
                      <p>
                        <span className={`status status-${job.status}`}>
                          {job.status}
                        </span>{" "}
                        <span className="quiet">
                          {fmt(job.records)} records processed
                        </span>
                      </p>
                      {job.error && (
                        <div className="message error">{job.error}</div>
                      )}
                      {ACTIVE.has(job.status) && (
                        <>
                          <div className="activity-line" />
                          <p>
                            Generating and verifying selected sources.
                            Completion includes checksum, schema, and
                            relationship checks.
                          </p>
                          <button
                            disabled={!!busy}
                            onClick={() =>
                              void task("Requesting cancellation", async () => {
                                await api(`/jobs/${job.id}/cancel`, {});
                                setNotice(
                                  "Cancellation requested. The worker will stop at its next checkpoint.",
                                );
                              })
                            }
                          >
                            Cancel run
                          </button>
                        </>
                      )}
                      {manifest && (
                        <>
                          <div className="result-metrics">
                            <div>
                              <strong>{fmt(manifest.total_records)}</strong>
                              <span>verified records</span>
                            </div>
                            <div>
                              <strong>
                                {manifest.elapsed_seconds.toFixed(1)}s
                              </strong>
                              <span>generation + verification</span>
                            </div>
                          </div>
                          <p className="verified">
                            ✓ Passed {manifest.verification.checks.length}{" "}
                            verification checks ·{" "}
                            {fmt(manifest.verification.unique_entities)} people
                            represented
                          </p>
                          <div className="table-wrap">
                            <table>
                              <thead>
                                <tr>
                                  <th>Source</th>
                                  <th>Rows</th>
                                  <th>Duplicate rows</th>
                                </tr>
                              </thead>
                              <tbody>
                                {Object.entries(manifest.counts).map(
                                  ([key, count]) => (
                                    <tr key={key}>
                                      <td>
                                        <button
                                          className="text-button"
                                          disabled={!!busy}
                                          onClick={() =>
                                            void task(
                                              "Loading sample",
                                              async () => {
                                                const value = await api<{
                                                  rows: Record<
                                                    string,
                                                    unknown
                                                  >[];
                                                }>(
                                                  `/jobs/${job.id}/sample/${key}`,
                                                );
                                                setSample(value.rows);
                                                setSampleSource(key);
                                              },
                                            )
                                          }
                                        >
                                          {LABELS[key]}
                                        </button>
                                      </td>
                                      <td>{fmt(count)}</td>
                                      <td>
                                        {(
                                          (manifest
                                            .realized_duplicate_row_fraction[
                                            key
                                          ] || 0) * 100
                                        ).toFixed(1)}
                                        %
                                      </td>
                                    </tr>
                                  ),
                                )}
                              </tbody>
                            </table>
                          </div>
                          {sampleSource && (
                            <details open>
                              <summary>
                                {LABELS[sampleSource]} · first {sample.length}{" "}
                                rows
                              </summary>
                              <div className="table-wrap sample-table">
                                <table>
                                  <thead>
                                    <tr>
                                      {Object.keys(sample[0] || {}).map(
                                        (key) => (
                                          <th key={key}>{key}</th>
                                        ),
                                      )}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {sample.map((row, index) => (
                                      <tr key={index}>
                                        {Object.values(row).map(
                                          (value, col) => (
                                            <td key={col}>
                                              {String(value ?? "")}
                                            </td>
                                          ),
                                        )}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                                {sample.length === 0 && (
                                  <p>This source has no rows.</p>
                                )}
                              </div>
                            </details>
                          )}
                          <div className="archive-download">
                            <a
                              className="download-button"
                              href={`/api/jobs/${job.id}/archive`}
                            >
                              Download complete run ↓
                            </a>
                            <small>
                              ZIP includes datasets, the answer key, and
                              reproducibility files.
                            </small>
                          </div>
                          <h3>Dataset files</h3>
                          <div className="downloads">
                            {Object.entries(manifest.files)
                              .filter(([, file]) => file.role === "dataset")
                              .map(([name, file]) => (
                                <a
                                  key={name}
                                  href={`/api/jobs/${job.id}/download/${name}`}
                                >
                                  {name}
                                  <small>{bytes(file.bytes)} ↓</small>
                                </a>
                              ))}
                          </div>
                          <details>
                            <summary>
                              Answer key, mutation history & reproducibility
                            </summary>
                            <p>
                              Keep the answer key separate from inputs used by
                              your matching system.
                            </p>
                            <div className="downloads">
                              {[
                                "ground_truth.csv",
                                "mutations.jsonl",
                                "scenario.json",
                                ...(manifest.files["environment.lock"]
                                  ? ["environment.lock"]
                                  : []),
                                "manifest.json",
                              ].map((name) => (
                                <a
                                  key={name}
                                  href={`/api/jobs/${job.id}/download/${name}`}
                                >
                                  {name}
                                  <span>↓</span>
                                </a>
                              ))}
                            </div>
                            <button
                              disabled={!!busy}
                              onClick={() =>
                                void task("Verifying run", async () => {
                                  await api(`/jobs/${job.id}/verify`, {});
                                  setNotice(
                                    "All run verification checks passed.",
                                  );
                                })
                              }
                            >
                              Verify files again
                            </button>
                          </details>
                          <details>
                            <summary>Publish to S3</summary>
                            <p>
                              Uses your local AWS credential chain. Files are
                              uploaded to a unique run prefix; the completion
                              manifest is written last.
                            </p>
                            <label>
                              Bucket name
                              <input
                                value={bucket}
                                onChange={(e) => setBucket(e.target.value)}
                                placeholder="my-demo-data"
                              />
                            </label>
                            <label>
                              Prefix
                              <input
                                value={prefix}
                                onChange={(e) => setPrefix(e.target.value)}
                              />
                            </label>
                            <label className="inline-check">
                              <input
                                type="checkbox"
                                checked={includeTruth}
                                onChange={(e) =>
                                  setIncludeTruth(e.target.checked)
                                }
                              />
                              Include answer key, mutation history, and tracer
                              input
                            </label>
                            <button
                              disabled={!!busy || !bucket}
                              onClick={() =>
                                void task("Publishing to S3", async () => {
                                  const result = await api<{
                                    prefix: string;
                                    already_published: boolean;
                                  }>(`/jobs/${job.id}/publish`, {
                                    bucket,
                                    prefix,
                                    include_truth: includeTruth,
                                  });
                                  setNotice(
                                    `${result.already_published ? "Already published" : "Published"} to s3://${bucket}/${result.prefix}`,
                                  );
                                })
                              }
                            >
                              Publish this run
                            </button>
                          </details>
                        </>
                      )}
                    </>
                  )}
                </section>
              </div>
            )}
          </>
        )}
        <footer>
          gensampledata{" "}
          <span>
            Deterministic scenarios. Explicit relationships. Inspectable
            imperfections.
          </span>
        </footer>
      </main>
    </div>
  );
}
