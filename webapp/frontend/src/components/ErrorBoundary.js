import React from "react";
import { html } from "../html.js";

// A real React error boundary -- getDerivedStateFromError/componentDidCatch
// are only available on a class component; there is no hook equivalent.
// Wrap each independent panel/widget in one of these (not just one
// boundary around the whole page) so a single broken widget renders its
// own "what failed, plus Retry" in place, instead of a render exception
// anywhere in the tree blanking the entire terminal.
export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
    this.retry = this.retry.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // console, not silently swallowed -- a caught render error is still a
    // real bug worth seeing in devtools, even though the UI itself
    // degrades gracefully instead of going blank.
    console.error(`ErrorBoundary caught an error in "${this.props.label || "panel"}":`, error, info);
  }

  retry() {
    // Clearing the error re-renders children fresh -- if the child owns
    // its own data-fetching (the normal shape in this codebase, see
    // AccountPanel.js's own load()), a fresh mount re-runs that fetch too.
    this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return html`
        <div class="error-state">
          <div>
            <div class="error-state-title">${this.props.label ? `${this.props.label} failed to render` : "Something failed to render"}</div>
            <div class="error-state-detail">${this.state.error?.message || String(this.state.error)}</div>
          </div>
          <button class="btn btn-sm btn-ghost" onClick=${this.retry}>Retry</button>
        </div>
      `;
    }
    return this.props.children;
  }
}
