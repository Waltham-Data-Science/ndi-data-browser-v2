import React from 'react';
import ReactDOM from 'react-dom/client';
// Self-hosted Geist (sans + mono) via fontsource. Registered as CSS
// font-family "Geist Variable" and "Geist Mono Variable" — matched in
// index.css's --font-sans / --font-mono tokens. Browser auto-loads
// only the Latin subset by default; Cyrillic / Latin-Ext subsets ship
// in the bundle but are gated behind unicode-range, so they only
// download when a character in that range is actually rendered.
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import { App } from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
