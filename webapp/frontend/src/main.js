import React from "react";
import { createRoot } from "react-dom/client";
import { html } from "./html.js";
import { App } from "./App.js";
import { ToastProvider } from "./toast.js";

const root = createRoot(document.getElementById("root"));
root.render(html`
  <${ToastProvider}>
    <${App} />
  <//>
`);
