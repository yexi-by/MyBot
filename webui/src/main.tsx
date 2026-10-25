import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@chinese-fonts/dyh/dist/SmileySans-Oblique/result.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource/ibm-plex-mono/latin-600.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/shell.css";
import "./styles/page.css";
import "./styles/overlays.css";
import "./styles/files.css";

import App from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("index.html 缺少 #root 挂载点");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
