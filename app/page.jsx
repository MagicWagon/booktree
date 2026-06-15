"use client";

import { useEffect, useMemo, useState } from "react";

const statusLabels = {
  needs_metadata: "Needs metadata",
  no_match: "No match",
  multiple_matches: "Multiple matches",
  needs_split_review: "Split review",
  matched: "Matched",
  processed: "Processed",
  failed: "Failed",
  ignored: "Ignored",
};

const editableFields = [
  ["asin", "ASIN"],
  ["title", "Title"],
  ["subtitle", "Subtitle"],
  ["authors", "Authors"],
  ["narrators", "Narrators"],
  ["series", "Series"],
  ["series_part", "Series part"],
  ["language", "Language"],
];

const configSections = [
  {
    title: "Metadata and Search",
    fields: [
      { key: "metadata", label: "Metadata", type: "select", options: ["mam-audible", "mam", "audible", "log"] },
      { key: "matchrate", label: "Match rate", type: "number" },
      { key: "fuzzy_match", label: "Fuzzy match", type: "select", options: ["token_sort", "partial", "ratio"] },
      { key: "log_path", label: "Log path" },
      { key: "cache_path", label: "Cache path" },
      { key: "last_scan", label: "Last scan" },
    ],
  },
  {
    title: "MAM and Mousehole",
    fields: [
      { key: "session", label: "MAM session", type: "secret" },
      { key: "mousehole_enabled", label: "Mousehole enabled", type: "boolean" },
      { key: "mousehole_state_file", label: "Mousehole state file" },
    ],
  },
  {
    title: "Flags",
    parent: "flags",
    fields: [
      { key: "dry_run", label: "Dry run", type: "boolean" },
      { key: "verbose", label: "Verbose", type: "boolean" },
      { key: "multibook", label: "Multibook", type: "boolean" },
      { key: "ebooks", label: "Ebooks", type: "boolean" },
      { key: "no_opf", label: "No OPF", type: "boolean" },
      { key: "no_cache", label: "No cache", type: "boolean" },
      { key: "fixid3", label: "Fix ID3", type: "boolean" },
      { key: "add_narrators", label: "Add narrators", type: "boolean" },
      { key: "interactive", label: "Interactive", type: "boolean" },
      { key: "hardlink", label: "Hardlink", type: "boolean" },
      { key: "ingest_calibre", label: "Ingest Calibre", type: "boolean" },
    ],
  },
  {
    title: "Target Paths",
    parent: "target_path",
    fields: [
      { key: "multi_author", label: "Multi-author" },
      { key: "in_series", label: "In series" },
      { key: "no_series", label: "No series" },
      { key: "disc_folder", label: "Disc folder" },
      { key: "calibre_ingest_path", label: "Calibre ingest path" },
    ],
  },
  {
    title: "Tokens",
    parent: "tokens",
    fields: [
      { key: "skip_series", label: "Skip series", type: "boolean" },
      { key: "kw_ignore", label: "Ignored characters", type: "array" },
      { key: "kw_ignore_words", label: "Ignored words", type: "array" },
      { key: "title_patterns", label: "Title patterns", type: "array" },
    ],
  },
];

function cloneConfig(config) {
  return JSON.parse(JSON.stringify(config || { Config: {} }));
}

function formatConfig(config) {
  return JSON.stringify(config || { Config: {} }, null, 2);
}

function parseArray(value) {
  return value
    .split(/\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

export default function Home() {
  const [stats, setStats] = useState({});
  const [books, setBooks] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const [targetGroupId, setTargetGroupId] = useState("");
  const [view, setView] = useState("books");
  const [configs, setConfigs] = useState([]);
  const [activeConfig, setActiveConfig] = useState("");
  const [configFile, setConfigFile] = useState(null);
  const [configData, setConfigData] = useState({ Config: {} });
  const [configJson, setConfigJson] = useState(formatConfig({ Config: {} }));
  const [configSchema, setConfigSchema] = useState({});
  const [selectedConfigPath, setSelectedConfigPath] = useState("");
  const [saveAsName, setSaveAsName] = useState("");
  const [showSession, setShowSession] = useState(false);

  const selectedBook = useMemo(
    () => detail?.book || books.find((book) => book.id === selectedId),
    [books, detail, selectedId]
  );

  async function refresh() {
    const [statPayload, bookPayload] = await Promise.all([
      requestJson("/api/stats"),
      requestJson(`/api/books?status=${encodeURIComponent(status)}&q=${encodeURIComponent(query)}`),
    ]);
    setStats(statPayload.counts || {});
    setBooks(bookPayload.books || []);
    if (!selectedId && bookPayload.books?.length) {
      setSelectedId(bookPayload.books[0].id);
    }
  }

  async function loadDetail(id = selectedId) {
    if (!id) {
      setDetail(null);
      return;
    }
    const payload = await requestJson(`/api/books/${id}`);
    setDetail(payload);
    setSelectedFileIds([]);
  }

  async function loadConfigList() {
    const payload = await requestJson("/api/configs");
    setConfigs(payload.configs || []);
    setActiveConfig(payload.active_config || "");
    setConfigSchema(payload.schema || {});
    const nextPath = selectedConfigPath || payload.active_config || payload.configs?.[0]?.path || "";
    if (nextPath && !configFile) {
      await loadConfig(nextPath);
    }
  }

  async function loadConfig(path = selectedConfigPath || activeConfig) {
    if (!path) {
      return;
    }
    const payload = await requestJson(`/api/configs/file?path=${encodeURIComponent(path)}`);
    const nextConfig = cloneConfig(payload.config);
    setConfigFile(payload.file);
    setSelectedConfigPath(payload.file.path);
    setConfigData(nextConfig);
    setConfigJson(formatConfig(nextConfig));
    setConfigSchema(payload.schema || configSchema);
  }

  async function run(label, action) {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      const result = await action();
      setMessage(result.message || "Done");
      await refresh();
      await loadDetail(result.book?.id || selectedId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function runConfig(label, action) {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      const result = await action();
      setMessage(result.message || "Done");
      await loadConfigList();
      if (result.file?.path) {
        await loadConfig(result.file.path);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, [status, query]);

  useEffect(() => {
    loadConfigList().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    loadDetail().catch((err) => setError(err.message));
  }, [selectedId]);

  useEffect(() => {
    if (busy) {
      return undefined;
    }
    const timer = setInterval(() => {
      refresh()
        .then(() => {
          if (selectedId) {
            return loadDetail(selectedId);
          }
          return undefined;
        })
        .catch((err) => setError(err.message));
    }, 10000);
    return () => clearInterval(timer);
  }, [busy, status, query, selectedId]);

  function updateField(field, value) {
    setDetail((current) => ({
      ...current,
      book: { ...current.book, [field]: value },
    }));
  }

  function toggleFile(id) {
    setSelectedFileIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    );
  }

  function selectedGroupIds() {
    return targetGroupId ? [Number(targetGroupId)] : [];
  }

  function updateConfigValue(parent, key, value) {
    const nextConfig = cloneConfig(configData);
    nextConfig.Config ||= {};
    if (parent) {
      nextConfig.Config[parent] ||= {};
      nextConfig.Config[parent][key] = value;
    } else {
      nextConfig.Config[key] = value;
    }
    setConfigData(nextConfig);
    setConfigJson(formatConfig(nextConfig));
  }

  function updatePathValue(index, key, value) {
    const nextConfig = cloneConfig(configData);
    nextConfig.Config ||= {};
    nextConfig.Config.paths ||= [];
    nextConfig.Config.paths[index] ||= {};
    nextConfig.Config.paths[index][key] = value;
    setConfigData(nextConfig);
    setConfigJson(formatConfig(nextConfig));
  }

  function addPathMapping() {
    const nextConfig = cloneConfig(configData);
    nextConfig.Config ||= {};
    nextConfig.Config.paths ||= [];
    nextConfig.Config.paths.push({ files: ["**/*.m4b", "**/*.mp3", "**/*.m4a"], source_path: "", media_path: "" });
    setConfigData(nextConfig);
    setConfigJson(formatConfig(nextConfig));
  }

  function removePathMapping(index) {
    const nextConfig = cloneConfig(configData);
    nextConfig.Config.paths = (nextConfig.Config.paths || []).filter((_, itemIndex) => itemIndex !== index);
    setConfigData(nextConfig);
    setConfigJson(formatConfig(nextConfig));
  }

  function parseConfigJson() {
    const parsed = JSON.parse(configJson);
    if (!parsed || typeof parsed !== "object" || !parsed.Config || typeof parsed.Config !== "object") {
      throw new Error("Config JSON must contain a Config object");
    }
    return parsed;
  }

  async function saveConfig() {
    const parsed = parseConfigJson();
    const result = await requestJson("/api/configs/file", {
      method: "PATCH",
      body: JSON.stringify({ path: configFile?.path || selectedConfigPath, config: parsed }),
    });
    setConfigData(cloneConfig(result.config));
    setConfigJson(formatConfig(result.config));
    return { ...result, message: "Config saved" };
  }

  async function saveConfigAs() {
    const parsed = parseConfigJson();
    const result = await requestJson("/api/configs", {
      method: "POST",
      body: JSON.stringify({ name: saveAsName, config: parsed }),
    });
    setSaveAsName("");
    setActiveConfig(result.active_config || activeConfig);
    return { ...result, message: "Config saved as active" };
  }

  async function setConfigActive() {
    return requestJson("/api/configs/active", {
      method: "POST",
      body: JSON.stringify({ path: configFile?.path || selectedConfigPath }),
    });
  }

  function fieldHelp(key) {
    return configSchema[key] || "Booktree config setting";
  }

  function renderConfigField(section, field) {
    const parent = section.parent || "";
    const sectionValue = parent ? configData.Config?.[parent] || {} : configData.Config || {};
    const value = sectionValue[field.key];
    const label = (
      <label>
        {field.label}
        <span className="help" title={fieldHelp(field.key)}>
          ?
        </span>
      </label>
    );
    if (field.type === "boolean") {
      return (
        <div className="field checkbox-field" key={`${parent}-${field.key}`}>
          {label}
          <input
            type="checkbox"
            checked={Boolean(Number(value || 0))}
            onChange={(event) => updateConfigValue(parent, field.key, event.target.checked ? 1 : 0)}
          />
        </div>
      );
    }
    if (field.type === "select") {
      return (
        <div className="field" key={`${parent}-${field.key}`}>
          {label}
          <select value={value ?? ""} onChange={(event) => updateConfigValue(parent, field.key, event.target.value)}>
            {field.options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
      );
    }
    if (field.type === "array") {
      return (
        <div className="field full" key={`${parent}-${field.key}`}>
          {label}
          <textarea
            value={Array.isArray(value) ? value.join("\n") : value || ""}
            onChange={(event) => updateConfigValue(parent, field.key, parseArray(event.target.value))}
          />
        </div>
      );
    }
    return (
      <div className={`field ${field.key === "session" ? "full" : ""}`} key={`${parent}-${field.key}`}>
        {label}
        <input
          type={field.type === "secret" && !showSession ? "password" : field.type === "number" ? "number" : "text"}
          value={value ?? ""}
          onChange={(event) =>
            updateConfigValue(parent, field.key, field.type === "number" ? Number(event.target.value) : event.target.value)
          }
        />
        {field.type === "secret" ? (
          <button className="inline-button" type="button" onClick={() => setShowSession((current) => !current)}>
            {showSession ? "Hide" : "Reveal"}
          </button>
        ) : null}
      </div>
    );
  }

  async function saveMetadata() {
    const payload = Object.fromEntries(
      editableFields.map(([field]) => [field, detail.book[field] || ""])
    );
    const result = await requestJson(`/api/books/${detail.book.id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    setDetail(result);
    return { ...result, message: "Metadata saved" };
  }

  async function search(provider) {
    const saved = await saveMetadata();
    const result = await requestJson(`/api/books/${saved.book.id}/search`, {
      method: "POST",
      body: JSON.stringify({ provider }),
    });
    const count = result.matches?.length || result.results?.reduce((sum, item) => sum + item.matches.length, 0) || 0;
    setDetail(result);
    return { ...result, message: `Search returned ${count} candidate${count === 1 ? "" : "s"}` };
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <h1>Booktree</h1>
          <p>Outstanding item review and reprocessing</p>
        </div>
        <div className="toolbar">
          <button
            onClick={() =>
              run("import", async () => ({
                ...(await requestJson("/api/import", { method: "POST", body: "{}" })),
                message: "Synced Booktree logs",
              }))
            }
            disabled={!!busy}
          >
            Sync Logs
          </button>
          <button
            onClick={() =>
              run("refresh", async () => {
                await refresh();
                return { message: "Refreshed" };
              })
            }
            disabled={!!busy}
          >
            Refresh
          </button>
        </div>
      </header>

      <nav className="tabs">
        <button className={view === "books" ? "active" : ""} onClick={() => setView("books")}>
          Books
        </button>
        <button className={view === "config" ? "active" : ""} onClick={() => setView("config")}>
          Config
        </button>
      </nav>

      {message ? <div className="message">{message}</div> : null}
      {error ? <div className="message error">{error}</div> : null}

      {view === "books" ? (
        <>
      <section className="summary-grid">
        {Object.entries(statusLabels).map(([key, label]) => (
          <button className="summary-card" key={key} onClick={() => setStatus(key)}>
            <strong>{stats[key] || 0}</strong>
            <span>{label}</span>
          </button>
        ))}
      </section>

      <section className="main-grid">
        <section className="panel">
          <div className="panel-header">
            <h2>Outstanding Items</h2>
            <span className="small">{stats.total || 0} tracked</span>
          </div>
          <div className="filters">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter by title, author, ASIN, or failure"
            />
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">All statuses</option>
              {Object.entries(statusLabels).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Book</th>
                  <th>Detected</th>
                  <th>Counts</th>
                  <th>Files</th>
                  <th>Status</th>
                  <th>Last searched</th>
                </tr>
              </thead>
              <tbody>
                {books.map((book) => (
                  <tr
                    key={book.id}
                    className={book.id === selectedId ? "selected" : ""}
                    onClick={() => setSelectedId(book.id)}
                  >
                    <td>
                      <div className="book-name">{book.name}</div>
                      <div className="small">{book.file}</div>
                    </td>
                    <td>
                      <div>{book.title || "Untitled"}</div>
                      <div className="small">{book.authors || "Unknown author"}</div>
                    </td>
                    <td>
                      <div className="small">MAM {book.mam_count || 0}</div>
                      <div className="small">Audible {book.audible_count || 0}</div>
                    </td>
                    <td className="small">{book.file_count || 1}</td>
                    <td>
                      <span className={`badge ${book.status}`}>{statusLabels[book.status] || book.status}</span>
                      {book.failure_reason ? <div className="small">{book.failure_reason}</div> : null}
                    </td>
                    <td className="small">{book.last_searched_at || "Never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!books.length ? <div className="empty-state">No books found yet. Run Booktree or sync existing logs to populate the queue.</div> : null}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Detail</h2>
            {selectedBook ? <span className={`badge ${selectedBook.status}`}>{statusLabels[selectedBook.status] || selectedBook.status}</span> : null}
          </div>
          {detail?.book ? (
            <div className="detail">
              <div className="form-grid">
                {editableFields.map(([field, label]) => (
                  <div className={`field ${field === "title" || field === "subtitle" ? "full" : ""}`} key={field}>
                    <label>{label}</label>
                    <input
                      value={detail.book[field] || ""}
                      onChange={(event) => updateField(field, event.target.value)}
                    />
                  </div>
                ))}
                <div className="field full">
                  <label>Source</label>
                  <input value={detail.book.source_path || ""} readOnly />
                </div>
                <div className="field full">
                  <label>Detection</label>
                  <input value={`${detail.book.detection_reason || "unknown"} (${detail.book.file_count || 0} files)`} readOnly />
                </div>
              </div>
              <div className="actions">
                <button onClick={() => run("save", saveMetadata)} disabled={!!busy}>
                  Save
                </button>
                <button onClick={() => run("mam", () => search("mam"))} disabled={!!busy}>
                  Search MAM
                </button>
                <button onClick={() => run("audible", () => search("audible"))} disabled={!!busy}>
                  Search Audible
                </button>
                <button className="primary" onClick={() => run("both", () => search("both"))} disabled={!!busy}>
                  Search Both
                </button>
                <button
                  onClick={() =>
                    run("process", async () => ({
                      ...(await requestJson(`/api/books/${detail.book.id}/process`, { method: "POST" })),
                      message: "Process job completed",
                    }))
                  }
                  disabled={!!busy}
                >
                  Process Book
                </button>
                <button
                  onClick={() =>
                    run("ignore", async () => ({
                      ...(await requestJson(`/api/books/${detail.book.id}/ignore`, { method: "POST" })),
                      message: "Marked ignored",
                    }))
                  }
                  disabled={!!busy}
                >
                  Mark Ignored
                </button>
              </div>
              <div className="file-drilldown">
                <div className="panel-header inline">
                  <h2>Files</h2>
                  <span className="small">{selectedFileIds.length} selected</span>
                </div>
                <div className="table-wrap compact">
                  <table>
                    <thead>
                      <tr>
                        <th></th>
                        <th>File</th>
                        <th>Detected</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail.files || []).map((file) => (
                        <tr key={file.id}>
                          <td>
                            <input
                              type="checkbox"
                              checked={selectedFileIds.includes(file.id)}
                              onChange={() => toggleFile(file.id)}
                            />
                          </td>
                          <td>
                            <div className="small">{file.file}</div>
                          </td>
                          <td>
                            <div>{file.title || file.book_name || "Untitled"}</div>
                            <div className="small">{file.authors || "Unknown author"}</div>
                          </td>
                          <td>
                            <span className={`badge ${file.status}`}>{statusLabels[file.status] || file.status}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="actions">
                  <button
                    onClick={() =>
                      run("split", async () => ({
                        ...(await requestJson(`/api/books/${detail.book.id}/split`, {
                          method: "POST",
                          body: JSON.stringify({ fileIds: selectedFileIds }),
                        })),
                        message: "Created a new group from selected files",
                      }))
                    }
                    disabled={!!busy || !selectedFileIds.length}
                  >
                    Split Selected
                  </button>
                  <select value={targetGroupId} onChange={(event) => setTargetGroupId(event.target.value)}>
                    <option value="">Target group</option>
                    {books
                      .filter((book) => book.id !== detail.book.id)
                      .map((book) => (
                        <option key={book.id} value={book.id}>
                          {book.name}
                        </option>
                      ))}
                  </select>
                  <button
                    onClick={() =>
                      run("combine", async () => ({
                        ...(await requestJson(`/api/books/${detail.book.id}/combine`, {
                          method: "POST",
                          body: JSON.stringify({ groupIds: selectedGroupIds() }),
                        })),
                        message: "Combined groups",
                      }))
                    }
                    disabled={!!busy || !targetGroupId}
                  >
                    Combine Target Into This
                  </button>
                  <button
                    onClick={() =>
                      run("move", async () => ({
                        ...(await requestJson(`/api/books/${detail.book.id}/move`, {
                          method: "POST",
                          body: JSON.stringify({ targetId: Number(targetGroupId), fileIds: selectedFileIds }),
                        })),
                        message: "Moved selected files",
                      }))
                    }
                    disabled={!!busy || !targetGroupId || !selectedFileIds.length}
                  >
                    Move Selected To Target
                  </button>
                </div>
              </div>
              {busy ? <div className="message">Working: {busy}</div> : null}
              <div className="matches">
                {(detail.matches || []).map((match) => (
                  <article className="match-card" key={match.id}>
                    <h3>{match.title || "Untitled match"}</h3>
                    <div className="small">
                      {match.provider} {match.asin ? `- ${match.asin}` : ""} {match.match_rate ? `- score ${match.match_rate}` : ""}
                    </div>
                    <div>{match.authors || "Unknown author"}</div>
                    {match.narrators ? <div className="small">Narrated by {match.narrators}</div> : null}
                    {match.series ? <div className="small">{match.series}</div> : null}
                    <div className="actions">
                      <button
                        className={match.is_accepted ? "primary" : ""}
                        onClick={() =>
                          run("accept", async () => ({
                            ...(await requestJson(`/api/books/${detail.book.id}/accept`, {
                              method: "POST",
                              body: JSON.stringify({ matchId: match.id }),
                            })),
                            message: "Match accepted",
                          }))
                        }
                        disabled={!!busy}
                      >
                        {match.is_accepted ? "Accepted" : "Accept Match"}
                      </button>
                    </div>
                  </article>
                ))}
                {!detail.matches?.length ? <div className="empty-state">No candidates yet. Search MAM, Audible, or both.</div> : null}
              </div>
            </div>
          ) : (
            <div className="empty-state">Select a book to review metadata and matches.</div>
          )}
        </section>
      </section>
        </>
      ) : (
        <section className="config-layout">
          <section className="panel">
            <div className="panel-header">
              <h2>Config Files</h2>
              {activeConfig ? <span className="small">Active: {activeConfig}</span> : null}
            </div>
            <div className="config-toolbar">
              <select
                value={selectedConfigPath}
                onChange={(event) => {
                  setSelectedConfigPath(event.target.value);
                  loadConfig(event.target.value).catch((err) => setError(err.message));
                }}
              >
                <option value="">Open config</option>
                {configs.map((file) => (
                  <option key={file.path} value={file.path}>
                    {file.name}
                  </option>
                ))}
              </select>
              <button onClick={() => runConfig("reload-config", () => loadConfig().then(() => ({ message: "Config reloaded" })))} disabled={!!busy || !selectedConfigPath}>
                Reload
              </button>
              <button className="primary" onClick={() => runConfig("save-config", saveConfig)} disabled={!!busy || !configFile}>
                Save
              </button>
              <button onClick={() => runConfig("set-active-config", setConfigActive)} disabled={!!busy || !configFile}>
                Set Active
              </button>
            </div>
            <div className="config-toolbar">
              <input
                value={saveAsName}
                onChange={(event) => setSaveAsName(event.target.value)}
                placeholder="testing.json"
              />
              <button onClick={() => runConfig("save-config-as", saveConfigAs)} disabled={!!busy || !saveAsName.trim()}>
                Save As
              </button>
            </div>
          </section>

          <section className="config-grid">
            <section className="panel">
              <div className="panel-header">
                <h2>Settings</h2>
                {configFile ? <span className="small">{configFile.name}</span> : null}
              </div>
              <div className="config-editor">
                {configSections.map((section) => (
                  <section className="config-section" key={section.title}>
                    <h3>{section.title}</h3>
                    <div className="form-grid">
                      {section.fields.map((field) => renderConfigField(section, field))}
                    </div>
                  </section>
                ))}

                <section className="config-section">
                  <div className="panel-header inline">
                    <h3>Paths</h3>
                    <button type="button" onClick={addPathMapping}>
                      Add Path
                    </button>
                  </div>
                  {(configData.Config?.paths || []).map((pathConfig, index) => (
                    <article className="path-card" key={index}>
                      <div className="form-grid">
                        <div className="field full">
                          <label>
                            Files
                            <span className="help" title={fieldHelp("files")}>
                              ?
                            </span>
                          </label>
                          <textarea
                            value={Array.isArray(pathConfig.files) ? pathConfig.files.join("\n") : pathConfig.files || ""}
                            onChange={(event) => updatePathValue(index, "files", parseArray(event.target.value))}
                          />
                        </div>
                        <div className="field">
                          <label>
                            Source path
                            <span className="help" title={fieldHelp("source_path")}>
                              ?
                            </span>
                          </label>
                          <input
                            value={pathConfig.source_path || ""}
                            onChange={(event) => updatePathValue(index, "source_path", event.target.value)}
                          />
                        </div>
                        <div className="field">
                          <label>
                            Media path
                            <span className="help" title={fieldHelp("media_path")}>
                              ?
                            </span>
                          </label>
                          <input
                            value={pathConfig.media_path || ""}
                            onChange={(event) => updatePathValue(index, "media_path", event.target.value)}
                          />
                        </div>
                      </div>
                      <div className="actions">
                        <button type="button" onClick={() => removePathMapping(index)} disabled={(configData.Config?.paths || []).length <= 1}>
                          Remove Path
                        </button>
                      </div>
                    </article>
                  ))}
                </section>
              </div>
            </section>

            <section className="panel">
              <div className="panel-header">
                <h2>Advanced JSON</h2>
                <span className="small">Unknown keys are preserved</span>
              </div>
              <div className="json-editor">
                <textarea value={configJson} onChange={(event) => setConfigJson(event.target.value)} spellCheck="false" />
                <div className="actions">
                  <button
                    type="button"
                    onClick={() =>
                      runConfig("apply-json", async () => {
                        const parsed = parseConfigJson();
                        setConfigData(cloneConfig(parsed));
                        setConfigJson(formatConfig(parsed));
                        return { message: "JSON applied to form" };
                      })
                    }
                    disabled={!!busy}
                  >
                    Apply JSON
                  </button>
                </div>
              </div>
            </section>
          </section>
        </section>
      )}
    </main>
  );
}
