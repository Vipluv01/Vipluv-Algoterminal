import React from "react";
import { html } from "../html.js";
import { marked } from "marked";
import { api } from "../api.js";
import { useToast } from "../toast.js";
import { useShortcuts } from "../keyboard.js";
import { Modal } from "../components/Modal.js";
import { EmptyState } from "../components/EmptyState.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { SkeletonBlock } from "../components/Skeleton.js";
import { pnlClass } from "../format.js";
import { pnl as fmtSignedPnl } from "../format.js";
import { useMode } from "../mode.js";

const TAG_FILTER_KEY = "algoterminal:journal:tagFilter";

function readTagFilter() {
  try {
    return window.localStorage.getItem(TAG_FILTER_KEY) || null;
  } catch {
    return null;
  }
}

function writeTagFilter(tag) {
  try {
    if (tag) window.localStorage.setItem(TAG_FILTER_KEY, tag);
    else window.localStorage.removeItem(TAG_FILTER_KEY);
  } catch {
    /* non-fatal -- storage may be disabled */
  }
}

// marked.parse() renders the note's markdown to HTML on the client; the
// text itself was already run through nh3.clean() server-side before it
// was ever stored (see app/routers/journal.py's create_note), so this is
// rendering SANITIZED markdown, not trusting raw HTML from the wire.
function NoteBody({ text }) {
  const htmlString = React.useMemo(() => marked.parse(text), [text]);
  return html`<div class="journal-note-body" dangerouslySetInnerHTML=${{ __html: htmlString }} />`;
}

function TradePicker({ value, onChange }) {
  const [orders, setOrders] = React.useState(null);
  const mode = useMode();

  // Scoped to the CURRENT trading mode -- a note written while looking at
  // a live trade should link to that live order, not have paper's orders
  // mixed into the same dropdown (see AccountPanel.js's own 2026-08-30
  // fix for the same underlying api.orders.list() mode-blindness).
  React.useEffect(() => {
    api.orders.list(mode).then(setOrders).catch(() => setOrders([]));
  }, [mode]);

  return html`
    <select class="input" value=${value ?? ""} onChange=${(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">No linked trade</option>
      ${(orders || []).map((o) => html`
        <option key=${o.id} value=${o.id}>#${o.id} — ${o.symbol} ${o.side} ${o.qty} @ ${o.px ?? "mkt"}</option>
      `)}
    </select>
  `;
}

function NewNoteModal({ onClose, onCreated }) {
  const [text, setText] = React.useState("");
  const [tagsInput, setTagsInput] = React.useState("");
  const [tradeId, setTradeId] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const toast = useToast();

  async function submit() {
    if (!text.trim()) return toast("Note text can't be empty", "err");
    const tags = tagsInput.split(",").map((t) => t.trim()).filter(Boolean);
    setSaving(true);
    try {
      const note = await api.journal.create({ text: text.trim(), tags: tags.length ? tags : null, trade_id: tradeId });
      onCreated(note);
      onClose();
    } catch (e) {
      toast(e.message || "Could not save the note", "err");
    } finally {
      setSaving(false);
    }
  }

  return html`
    <${Modal} title="New Journal Entry" onClose=${onClose} size="lg">
      <div style=${{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <div class="field">
          <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>Entry (Markdown supported)</label>
          <textarea class="input" rows="8" value=${text} onInput=${(e) => setText(e.target.value)}
                    placeholder="What happened, and why?" style=${{ fontFamily: "var(--font-mono)", resize: "vertical" }} />
        </div>
        <div class="field">
          <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>Tags (comma-separated)</label>
          <input class="input" value=${tagsInput} onInput=${(e) => setTagsInput(e.target.value)} placeholder="thesis, mistake, review" />
        </div>
        <div class="field">
          <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>Link to a trade</label>
          <${TradePicker} value=${tradeId} onChange=${setTradeId} />
        </div>
        <div style=${{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
          <button class="btn btn-ghost" onClick=${onClose} disabled=${saving}>Cancel</button>
          <button class="btn btn-primary" onClick=${submit} disabled=${saving}>${saving ? "Saving…" : "Save Entry"}</button>
        </div>
      </div>
    <//>
  `;
}

function NoteCard({ note, onDelete }) {
  const [deleting, setDeleting] = React.useState(false);

  async function remove() {
    setDeleting(true);
    try {
      await onDelete(note.id);
    } finally {
      setDeleting(false);
    }
  }

  return html`
    <div class="panel panel-pad" style=${{ marginBottom: "12px" }}>
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "10px", marginBottom: "8px" }}>
        <div style=${{ color: "var(--text-faint)", fontSize: "11px" }}>${new Date(note.created_at).toLocaleString()}</div>
        <div style=${{ display: "flex", gap: "8px", alignItems: "center" }}>
          ${note.trade_id != null && html`<span class="chip static">Trade #${note.trade_id}</span>`}
          ${note.pnl_snapshot != null && html`
            <span class=${`chip static mono ${pnlClass(note.pnl_snapshot)}`}>${fmtSignedPnl(note.pnl_snapshot)}</span>
          `}
          <button class="btn btn-sm btn-ghost" onClick=${remove} disabled=${deleting} aria-label="Delete entry">✕</button>
        </div>
      </div>
      <${NoteBody} text=${note.text} />
      ${note.tags && note.tags.length > 0 && html`
        <div style=${{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "10px" }}>
          ${note.tags.map((t) => html`<span key=${t} class="chip static">${t}</span>`)}
        </div>
      `}
    </div>
  `;
}

export function Journal() {
  const [notes, setNotes] = React.useState(null); // null = loading
  const [error, setError] = React.useState(null);
  const [tagFilter, setTagFilterState] = React.useState(readTagFilter);
  const [search, setSearch] = React.useState("");
  const [modalOpen, setModalOpen] = React.useState(false);
  const toast = useToast();

  const load = React.useCallback(() => {
    setError(null);
    api.journal.list()
      .then(setNotes)
      .catch((e) => setError(e.message || "Could not load the journal"));
  }, []);
  React.useEffect(() => { load(); }, [load]);

  function setTagFilter(tag) {
    setTagFilterState(tag);
    writeTagFilter(tag);
  }

  const bindings = React.useMemo(() => [
    { chord: "n", description: "New journal entry", group: "Journal", handler: () => setModalOpen(true) },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], []);
  useShortcuts(bindings);

  const allTags = React.useMemo(() => {
    if (!notes) return [];
    const s = new Set();
    for (const n of notes) for (const t of n.tags || []) s.add(t);
    return Array.from(s).sort();
  }, [notes]);

  // The stored tag filter can name a tag no longer present on any note
  // (its last note was deleted, or it was typed once and never reused) --
  // fall back to "all" rather than silently showing zero rows forever.
  const effectiveTagFilter = tagFilter && allTags.includes(tagFilter) ? tagFilter : null;

  const filtered = React.useMemo(() => {
    if (!notes) return [];
    return notes.filter((n) => {
      if (effectiveTagFilter && !(n.tags || []).includes(effectiveTagFilter)) return false;
      if (search.trim() && !n.text.toLowerCase().includes(search.trim().toLowerCase())) return false;
      return true;
    });
  }, [notes, effectiveTagFilter, search]);

  async function deleteNote(id) {
    try {
      await api.journal.delete(id);
      setNotes((prev) => prev.filter((n) => n.id !== id));
    } catch (e) {
      toast(e.message || "Could not delete the entry", "err");
    }
  }

  function onCreated(note) {
    setNotes((prev) => (prev ? [note, ...prev] : [note]));
  }

  return html`
    <div class="page fade-in">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "10px", marginBottom: "20px" }}>
        <div>
          <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Journal</h1>
          <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>Markdown notes, tagged and optionally linked to a trade — the one place notes live.</div>
        </div>
        <button class="btn btn-primary" onClick=${() => setModalOpen(true)}>+ New Entry <span class="shortcut-hint">n</span></button>
      </div>

      ${notes !== null && html`
        <div style=${{ display: "flex", gap: "10px", flexWrap: "wrap", marginBottom: "16px", alignItems: "center" }}>
          <input class="input" style=${{ maxWidth: "260px" }} placeholder="Search entries…" value=${search}
                 onInput=${(e) => setSearch(e.target.value)} />
          ${allTags.length > 0 && html`
            <div style=${{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              <button class=${`chip ${!effectiveTagFilter ? "active" : ""}`} onClick=${() => setTagFilter(null)}>All</button>
              ${allTags.map((t) => html`
                <button key=${t} class=${`chip ${effectiveTagFilter === t ? "active" : ""}`} onClick=${() => setTagFilter(t)}>${t}</button>
              `)}
            </div>
          `}
        </div>
      `}

      <${ErrorBoundary} label="Journal">
        ${notes === null && !error && html`
          <${React.Fragment}>
            <${SkeletonBlock} height="120px" style=${{ marginBottom: "12px" }} />
            <${SkeletonBlock} height="120px" style=${{ marginBottom: "12px" }} />
            <${SkeletonBlock} height="120px" />
          <//>
        `}
        ${error && html`
          <div class="error-state">
            <div>
              <div class="error-state-title">Could not load the journal</div>
              <div class="error-state-detail">${error}</div>
            </div>
            <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
          </div>
        `}
        ${notes !== null && !error && filtered.length === 0 && html`
          <${EmptyState}
            message=${notes.length === 0 ? "No journal entries yet." : "No entries match this filter."}
            actionLabel=${notes.length === 0 ? "New Entry" : "Clear Filters"}
            onAction=${notes.length === 0 ? () => setModalOpen(true) : () => { setTagFilter(null); setSearch(""); }} />
        `}
        ${notes !== null && !error && filtered.map((n) => html`<${NoteCard} key=${n.id} note=${n} onDelete=${deleteNote} />`)}
      <//>

      ${modalOpen && html`<${NewNoteModal} onClose=${() => setModalOpen(false)} onCreated=${onCreated} />`}
    </div>
  `;
}
