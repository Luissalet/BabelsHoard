import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { startCopilot } from "./copilot";
import "./hoard-theme.css";
import "./styles.css";

startCopilot();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
