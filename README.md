# Sauron Presentation

This small folder contains a minimal static demo to preview the `map_status` feed used by the Sauron project.

Usage:

- Serve this folder (for example with `python -m http.server 8000`) and open `index.html` in your browser.
- The page loads `sample_data/map_status_sample.json` and renders it.
# Sauron — Presentation (Sample Data)

This repository is a lightweight, presentation-ready snapshot of the Sauron network scanner intended for public-facing demos and sample data.

Contents
- `index.html` — simple static map view referencing sample data (works from a static server).
- `sample_data/map_status_sample.json` — trimmed sample feed used by the presentation.
- `.gitignore` — ignores node_modules and common files.

How to use
1. Start a simple static server in this folder (recommended: `live-server` or `python -m http.server 5500`).
2. Open `index.html` in your browser (or visit `http://127.0.0.1:5500/index.html`).

Make it your own
- Replace `map_status_sample.json` with your own exported `map_status_latest.json` for a live demo.
- Edit `index.html` to adjust styling or the Kepler configuration.

License
This folder is intended for demo content — add a license file if you publish to GitHub.
