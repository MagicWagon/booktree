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

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, [status, query]);

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

      <section className="summary-grid">
        {Object.entries(statusLabels).map(([key, label]) => (
          <button className="summary-card" key={key} onClick={() => setStatus(key)}>
            <strong>{stats[key] || 0}</strong>
            <span>{label}</span>
          </button>
        ))}
      </section>

      {message ? <div className="message">{message}</div> : null}
      {error ? <div className="message error">{error}</div> : null}

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
    </main>
  );
}
