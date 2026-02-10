# Sauron — Presentation Demo

A lightweight, presentation-ready sample of the **Sauron** network scanner, designed for live demos and stakeholder presentations.

## What You're Looking At

This repo contains:
- **index.html** — Interactive geospatial dashboard showing store network health
- **sample_data/map_status_sample.json** — Sample scan results (green ✓, yellow ⚠, red ✗ statuses)
- **sauron.py** — Scanner tool (simplified demo version showing CLI interface)

No proprietary store data is included. Provide your own `stores.csv` and optional `DC_LIST.csv`.

## Quick Start (Demo Mode)

### View the Dashboard

1. **Start a local web server** in this folder:
   ```bash
   # Windows
   cd path\to\sauron-presentation
   python -m http.server 8000

   # Linux/macOS
   cd path/to/sauron-presentation
   python -m http.server 8000
   ```

2. **Open your browser** to:
   ```
   http://localhost:8000/index.html
   ```

3. **You should see:**
   - A geospatial map of the US (Maplibre GL)
   - Color-coded store markers:
     - **Green** = online ✓
     - **Yellow** = server down, gateway up (⚠ network issue)
     - **Red** = fully offline ✗
   - A left-hand panel listing failed stores grouped by status
   - Store details on click (address, last ping time, etc.)

### Run the Scanner (Locally)

To scan your own stores with your data:

1. **Ensure Python 3.10+ is installed:**
   ```bash
   python --version
   ```

2. **Run the scanner:**
   ```bash
   # Windows
   python sauron.py stores.csv

   # Linux/macOS
   python sauron.py stores.csv
   ```

3. **Or with gateway diagnostics:**
   ```bash
   # Windows
   python sauron.py stores.csv --gateway-check

   # Linux/macOS
   python sauron.py stores.csv --gateway-check
   ```

4. **Output files appear in `./logs/map_status_latest.json`**

### Required CSV Headers
`stores.csv` must include:
- `StoreNumber`
- `IPAddress`

Optional (recommended):
- `Gateway`, `Latitude`, `Longitude`, `Address`, `City`, `State`, `ZIP`

## Customizing the Demo

### Update the Sample Data

Replace `sample_data/map_status_sample.json` with your own scan results:

```bash
# Run a real scan in your environment
python sauron.py your_stores.csv --gateway-check --output-dir ./logs

# Copy the latest feed to the sample directory
# Windows
copy logs\map_status_latest.json sample_data\map_status_sample.json

# Linux/macOS
cp logs/map_status_latest.json sample_data/map_status_sample.json

# Reload the browser — dashboard updates automatically
```

### Modify Dashboard Styling

Edit `index.html` to adjust:
- **Color scheme** — change RGB values for green/yellow/red markers
- **Map center** — default view location (line ~450: `setCenter()`)
- **Popup text** — store information display format
- **Panel layout** — left-hand offline list appearance

### Sample Data Structure

Format your JSON feed like `sample_data/map_status_sample.json`:

```json
[
  {
    "timestamp": "2026-02-08T14:30:45.123456-05:00",
    "run_id": "20260208_143045",
    "store": "1001002",
    "dc_code": "1001",
    "dc_name": "Columbus",
    "server_ip": "10.115.191.10",
    "gateway_ip": "10.115.191.1",
    "server_up": true,
    "gateway_up": true,
    "status": "green",
    "status_code": 0,
    "Latitude": 40.088939,
    "Longitude": -83.061863,
    "City": "Columbus",
    "State": "OH"
  }
]
```

## Presentation Tips

- **Pre-scan before the meeting:** Run `python sauron.py stores.csv --gateway-check` to generate fresh data (5-10 minutes for typical network)
- **Use `--max-workers 500` to speed up large scans** (completes sooner)
- **Export the map:** Use browser's screenshot or developer tools to capture dashboard for slides
- **Show the offline panel:** Click a failed store to highlight it on the map — demonstrates geo-centric triage
- **Highlight the yellow group:** Shows how the tool distinguishes server failures (red) from network problems (yellow)

## Key Points for Stakeholders

| Feature | Value |
|---------|-------|
| **Speed** | 5,000+ stores in 2–5 minutes (parallelized) |
| **Accuracy** | Dual-check (ping + retry on timeout) |
| **Intelligence** | Distinguishes hardware failure from network outage |
| **Visibility** | Geo-centric dashboard: pinpoint affected regions instantly |
| **Integration** | JSON + CSV outputs for Power BI, Tableau, custom dashboards |
| **Lightweight** | Runs on any OS (Windows/Linux); Python only; no external dependencies |

## Next Steps

- **Production Deployment:** See the full [Sauron repository](https://github.com/decalius/Sauron) for comprehensive documentation
- **Internal Docs:** Check the [internal repo](https://github.com/decalius/Sauron-GPC) for architecture and deployment guides
- **Questions?** Refer to the full README in the main repository for troubleshooting and advanced options

---

**Version:** 3.0 | **Last Updated:** February 2026 | **Status:** Demo Ready

## Example Paths (No Local Paths)
Use these placeholders instead of real machine paths:

```text
Windows:
   path\to\sauron-presentation\
   path\to\sauron-presentation\logs\

Linux/macOS:
   path/to/sauron-presentation/
   path/to/sauron-presentation/logs/
```

## CLI Flags
```
stores_csv                 Path to stores CSV (default: stores.csv)
--dc-csv CSV_FILE          Path to DC list CSV (default: DC_LIST.csv)
--gateway-check            Enable gateway connectivity checks
--retry-pings N            Number of retries per failure (default: 3)
--timeout-ms MS            Ping timeout in milliseconds (default: 1000)
--max-workers N            Number of parallel ping threads (default: 200)
--output-dir PATH          Directory for logs/results (default: ./logs)
--publish-dir PATH         Where to publish live feed files (default: output dir)
--run-id ID                Custom run identifier (default: timestamp)
--write-txt                Export failure details as text report
--write-csv                Export failure details as CSV
--quiet                    Reduce console output
--zip-run                  Compress the run folder when complete
--remove-run-folder-after-zip  Delete run folder after zip
--loop                     Run continuously
--interval-seconds N       Loop sleep interval in seconds (default: 100)
--help                     Show all options
```
