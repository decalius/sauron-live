# Image Capture Guide for Sauron

This guide helps you capture professional screenshots and GIFs for the Sauron documentation.

## Quick Setup for Capturing

### Step 1: Start the Dashboard

```bash
# Start local web server
python -m http.server 8000

# Open browser to http://localhost:8000/index.html
```

### Step 2: Capture Screenshots

#### For `dashboard-overview.png`:

1. **Set browser to full screen** (F11 on most browsers)
2. **Ensure the view includes:**
   - The full geospatial map centered on the US (or your region)
   - Multiple site markers visible (green, yellow, red)
   - The left-hand offline panel with site list
   - A few sites clicked to show popup details (optional)

3. **Take the screenshot:**
   - **Windows:** Windows + Shift + S (Snipping Tool)
   - **macOS:** Cmd + Shift + 4 (Selection) or Cmd + Shift + 3 (Full screen)
   - **Linux:** Shift + PrtScr or use `gnome-screenshot`

4. **Save as:** `dashboard-overview.png` in the `assets/` directory

#### For `offline-panel.png`:

1. **Focus on the left panel** showing failed sites
2. **Ensure visible:**
   - List of offline sites
   - Status indicators (red ✗, yellow ⚠)
   - Site details (site number, DC code, etc.)
   - Grouping by failure type

3. **Capture just the panel area** (or crop the full screenshot)
4. **Save as:** `offline-panel.png` in the `assets/` directory

### Step 3: Record Demo GIF

#### Recommended Tools:

- **Windows:** ScreenToGif (free, open-source)
- **macOS:** Kap (free, open-source) or Gifox
- **Linux:** Peek (free, open-source)
- **Online:** Recordscreen.io or similar web-based tools

#### What to Record (10-30 seconds):

1. **Start with dashboard loaded** (zoomed out showing multiple sites)
2. **Click on a green site marker** → popup appears with "online" status
3. **Click on a yellow site marker** → popup shows "server down, gateway up"
4. **Click on a red site marker** → popup shows "fully offline"
5. **Pan the map slightly** to show interactivity (optional)
6. **Zoom in/out** to demonstrate the geospatial features (optional)

#### Recording Settings:

- **Resolution:** 1280x720 (720p) - balances quality and file size
- **Frame rate:** 10-15 fps (smooth enough, keeps file small)
- **Duration:** 10-30 seconds maximum
- **File size:** Keep under 5MB

#### Post-Processing:

If your GIF is too large:

1. **Use an optimizer:**
   - Online: [ezgif.com/optimize](https://ezgif.com/optimize)
   - CLI: `gifsicle -O3 --colors 256 input.gif -o output.gif`

2. **Reduce resolution** if needed (1024x576 is acceptable)

3. **Lower frame rate** to 10 fps if still too large

4. **Save as:** `demo.gif` in the `assets/` directory

## File Checklist

After capturing, verify you have:

- [ ] `assets/dashboard-overview.png` (full dashboard view)
- [ ] `assets/offline-panel.png` (offline sites panel)
- [ ] `assets/demo.gif` (interactive demonstration)
- [ ] All files under 5MB each
- [ ] Images are clear and readable
- [ ] GIF loops smoothly

## Commit Your Images

```bash
git add assets/
git commit -m "Add screenshots and demo GIF"
git push
```

The README.md already references these files, so they'll appear automatically once committed!

## Professional Tips

### For Screenshots:
- Use a clean browser window (hide bookmarks bar, extensions)
- Ensure good color contrast for visibility
- Capture at native resolution (no scaling/zoom)
- Use PNG format (lossless, crisp text)

### For GIFs:
- Record at consistent speed (no rushing)
- Show one feature at a time clearly
- Pause briefly between actions (gives viewers time to process)
- Keep cursor movements smooth and purposeful
- End with a complete view (dashboard reset to start state)

### Color Coding to Highlight:
Make sure your captures show the key differentiators:
- **Green markers** = Everything working (server + gateway up)
- **Yellow markers** = Network issue (server down, gateway up)
- **Red markers** = Complete failure (server + gateway down)

This color scheme is the core intelligence of Sauron!
