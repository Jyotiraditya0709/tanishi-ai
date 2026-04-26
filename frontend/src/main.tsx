import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";

if (typeof window !== "undefined" && window.speechSynthesis) {
  window.speechSynthesis.getVoices();
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
