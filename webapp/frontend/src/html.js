// JSX-like syntax with zero build step: htm compiles tagged template
// literals to React.createElement calls at runtime. esm.sh's htm/react
// build already exports a pre-bound `html` tag -- re-exported here so
// every component imports from one local module rather than the CDN URL
// directly, keeping the import map as the only place a version is pinned.
export { html } from "htm/react";
