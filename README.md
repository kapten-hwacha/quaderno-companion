# Quaderno Companion 📖✨

An autonomous background agent and zero-friction desktop bridge for the **Fujitsu Quaderno Gen 2** (A4 & A5 models) over local network protocols (Wi-Fi, Wi-Fi Access Point / SoftAP, and USB).

The agent ingests web pages, academic papers, and reading materials, optimizes them for E-ink rendering (margin trimming, native dimension scaling, Floyd-Steinberg dithering, and payload compression to <300 KB), pushes them to the device with zero friction, and controls reading navigation programmatically.

```
┌─────────────────────────────────────────────────────────────┐
│                       Input Triggers                        │
│  (Browser Extension / Bookmarklet / Raycast / CLI / Hotkeys)│
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / JSON API
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Local Daemon                     │
│                    (localhost:5000)                         │
├──────────────────────────────┬──────────────────────────────┤
│          Agent Core          │       REST API Endpoints     │
│ • Intent Routing             │ • POST /api/documents/open   │
│ • LLM Tool Calling Engine    │ • POST /api/viewer/page      │
│ • Reading State Memory       │ • GET  /api/viewer/status    │
│ • Summarize & Synthesize     │ • POST /api/agent/push       │
└───────────────┬──────────────┴──────────────┬───────────────┘
                │                             │
                ▼                             ▼
┌───────────────────────────────┐ ┌───────────────────────────┐
│     E-Ink Pipeline Tool       │ │   Quaderno Bridge Tool    │
│ • ArXiv / URL / HTML Fetcher  │ │ • Network Auto-Router     │
│ • Readability / Boilerplate   │ │   (Wi-Fi ➜ AP ➜ USB)      │
│ • PyMuPDF Geometry Scaler     │ │ • `dpt-rp1-py` Low-Level  │
│ • Floyd-Steinberg / Grayscale │ │ • Open2 & Viewer Control  │
│ • Payload Compressor (<300KB) │ │ • Persistent Auth Session │
└───────────────────────────────┘ └───────────┬───────────────┘
                                              │ HTTPS (SSL Client Certs)
                                              ▼
                                ┌───────────────────────────┐
                                │  Fujitsu Quaderno Gen 2   │
                                └───────────────────────────┘
```

---

## Features

- **Hardware Tailored**: Native resolution scaling for Quaderno A4 (1650 × 2200, 207 DPI) and A5 (1404 × 1872, 227 DPI).
- **Intelligent Margin Trimming**: Detects content bounding box and trims excess academic paper margins for maximum text readability.
- **Ultra-Fast Wireless Push**: Deflates and compresses PDF streams down to <300 KB for near-instant wireless transfer.
- **Network Auto-Routing**: Automatically discovers and fails over across local Wi-Fi (`digitalpaper.local` or static IP), Quaderno Wi-Fi Access Point / SoftAP (`192.168.43.1` / dynamic gateway), and USB tethering (`172.25.47.1`).
- **Autonomous Agent & Dual-Engine Summarizer**: Synthesizes structured 1–5 page executive briefs from text/URLs directly into high-contrast E-ink layouts via either **⚡ Gemini API** (instant ~2s briefs) or **📚 Google Gemini Notebook** (NotebookLM direct RPC with source-grounded citations).
- **Google Gemini Notebook Integration**: Automates source ingestion, grounding synthesis, and summary extraction using ephemeral or shared Gemini Notebooks via direct async RPCs.
- **1-Click Triggers**: Browser bookmarklet, Chrome extension (Manifest V3), and Raycast script commands for desktop workflows.

---

## Connection Methods

| Method | Protocol / IP | Best For | Details |
|---|---|---|---|
| 🌐 **Shared Wi-Fi** *(Recommended)* | `digitalpaper.local` or DHCP IP | Everyday home / office use | Connect both Mac and Quaderno to your standard Wi-Fi network. Mac keeps full internet access while syncing in the background. |
| 📡 **Wi-Fi Access Point (SoftAP)** | `192.168.43.1` / Default Gateway | Traveling / offline without a router | Turn on **Wi-Fi Access Point** in Quaderno settings. Join the Quaderno's Wi-Fi network from your Mac; the companion auto-discovers the gateway. |
| 🔌 **USB-C Cable** | `172.25.47.1` (CDC-NCM / RNDIS) | Fast sync & large batches | Instantaneous, zero network configuration required. Plug in via USB-C and sync immediately while charging. |

> [!NOTE]
> **macOS & Bluetooth**: On modern macOS, Apple has removed kernel support for the legacy **Bluetooth PAN (BNEP)** network profile. While macOS will successfully pair with the Quaderno at the hardware Bluetooth layer (`FMVDP41_...`), macOS will not route TCP/IP traffic over Bluetooth. For wireless operation on Mac, use **Shared Wi-Fi** or the **Quaderno Wi-Fi Access Point**.

## Quickstart & Installation

The primary way to use Quaderno Companion is as a **native macOS Menu Bar app** that automatically launches on boot, sits quietly in your menu bar, runs the background sync engine, and hosts the local API for browser/desktop triggers.

### Option A: Standalone macOS App (`.dmg`) *(Recommended)*

1. **Download & Install**:
   - Download **`Quaderno-Companion-v0.1.0.dmg`** from [Releases](https://github.com/your-username/quaderno-companion/releases).
   - Open the `.dmg` and drag **Quaderno Companion** into your **`/Applications`** folder.

2. **Auto-Start on Boot**:
   - Open **System Settings** ➜ **General** ➜ **Login Items & Extensions**.
   - Under **"Open at Login"**, click **`+`** and add **`/Applications/Quaderno Companion.app`**.
   - *(Or via Terminal)*:
     ```bash
     osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/Quaderno Companion.app", hidden:false}'
     ```

3. **Device Pairing (One-Time)**:
   - On your Quaderno: Go to **Settings** ➜ **Wi-Fi** / **Device Configuration** and locate the **Pairing PIN**.
   - Click the Quaderno menu bar icon ➜ click **"Pair Device..."** (or run `quadctl pair`).

---

### Option B: Automatic macOS Service via `uv` *(For Developers / Terminal Users)*

If running from source or via the `uv` toolchain, you can install the background LaunchAgent service in a single step:

```bash
# 1. Clone repository & install dependencies
git clone https://github.com/your-username/quaderno-companion.git
cd quaderno-companion
uv sync

# 2. Run the interactive setup wizard (pairs device, configures API key, & installs LaunchAgent)
uv run quadctl setup
```

Or install the auto-starting background service directly:

```bash
# 1. One-time device pairing:
uv run quadctl pair

# 2. Install background macOS LaunchAgent (auto-starts menu bar app + daemon on every login):
uv run quadctl install-service
```

> [!TIP]
> To stop or remove the auto-starting background service at any time: `uv run quadctl uninstall-service`.

---

## CLI Usage (`quadctl`)

| Command | Description |
|---|---|
| `uv run quadctl setup` | Interactive full setup wizard (Pairing + Gemini API Key + LaunchAgent) |
| `uv run quadctl setup-api` | Configures Google Gemini API key and model selection |
| `uv run quadctl pair` | Pairs computer with Quaderno device using PIN |
| `uv run quadctl app` | Launches native macOS Menu Bar background app |
| `uv run quadctl sync` | Runs an immediate bidirectional sync pass with `~/Quaderno` |
| `uv run quadctl open` | Opens the local Quaderno mirror folder in macOS Finder |
| `uv run quadctl push [url_or_path]` | Auto-detects active browser tab (Firefox, Safari, Chrome) or pushes URL/file |
| `uv run quadctl window` | Captures the currently active macOS window and pushes it to Quaderno |
| `uv run quadctl preview` | Pushes/mirrors the document active in Apple Preview |
| `uv run quadctl preview --watch` | Continuous real-time page mirror with Apple Preview |
| `uv run quadctl summarize <url_or_text>` | Generates a 1–5 page E-ink brief via Gemini Notebook / API and pushes to display |
| `uv run quadctl notebook login` | Logs in to Google Gemini Notebook (NotebookLM) via browser session |
| `uv run quadctl notebook status` | Checks Gemini Notebook authentication and library status |
| `uv run quadctl notebook list` | Lists notebooks in your Google Gemini Notebook library |
| `uv run quadctl install-service` | Installs background auto-start daemon (macOS LaunchAgent) |
| `uv run quadctl uninstall-service` | Removes background macOS LaunchAgent |
| `uv run quadctl serve` | Starts the FastAPI daemon |
| `uv run quadctl next` | Advances to the next page on the Quaderno |
| `uv run quadctl prev` | Returns to the previous page on the Quaderno |
| `uv run quadctl goto <page>` | Jumps to a specific page number |
| `uv run quadctl status` | Shows connection status, battery, storage, and active page |
| `uv run quadctl optimize <in> <out>` | Optimizes a local PDF with margin trimming and scaling |

---

## Desktop & Browser Triggers

### 1. 🌐 Zero-Extension Active Browser Tab Detection (Firefox, Safari, Chrome, Arc)
Push what you are reading straight to your Quaderno with zero browser extensions:

```bash
# Push the currently active web page from your browser:
uv run quadctl push
```

- **Firefox**: Reads active tab title and URL directly from Firefox's native session store (`recovery.jsonlz4`).
- **Safari / Chrome / Arc / Brave / Edge**: Bridges with macOS AppleEvents to grab the frontmost active tab.

---

### 2. 🖥️ Active Window Push
Capture any open desktop application window (IDE, terminal, notes, chart) and push it directly to your Quaderno with high-contrast Floyd-Steinberg E-ink dithering:

```bash
uv run quadctl window
```

---

### 3. 🔄 Bidirectional Local Folder Mirror (`~/Quaderno`)
Continuous local synchronization between macOS and your Quaderno storage:

```bash
# Trigger an immediate manual sync pass:
uv run quadctl sync

# Reveal your local Quaderno folder in macOS Finder:
uv run quadctl open
```
- **Local Mirror Directory**: Local folder located at `~/Quaderno` (outside iCloud Drive to prevent file evictions and sync conflicts).
- **Automatic Background Sync**: Continuously synchronizes in the background when the daemon or menu bar app is active.
- **Pull Notes & Markups**: Handwritten annotations and newly created notes on your Quaderno are automatically pulled to your Mac for offline search and backup.
- **Drag & Drop Upload**: Drag any PDF (or Markdown/text/image) into `~/Quaderno/` to auto-optimize and push to the device.
- **Conflict Safe**: Concurrent edits generate timestamped conflict copies (`file (Quaderno Conflict YYYYMMDD_HHMMSS).pdf`).

---

### 4. 🍎 Native macOS Menu Bar Background App
Run the companion directly in your macOS top menu bar:

```bash
uv run quadctl app
```

- **Live Battery & Document State**: Displays Quaderno battery level and current document in the menu bar.
- **Single-Row Page Navigation**: `[ ◀ Prev ] [ 🔢 Go to ] [ Next ▶ ]` directly in the top subdivision.
- **🔄 Sync Now**: 1-click trigger to run an immediate background sync pass.
- **📁 Open Quaderno Folder**: 1-click reveal of `~/Quaderno` in Finder.
- **🌐 Push Active Browser Tab**: 1-click grab and send your active tab from Firefox, Safari, or Chrome.
- **🖥️ Push Active Window**: 1-click capture and send the frontmost application window.
- **🪞 Preview Mirror**: Checkbox toggle to automatically mirror pages in real-time as you scroll/navigate in Apple Preview.
- **📝 Summarize Length**: Interactive page slider (Off, 1–5 pages) for custom briefing depth.
- **⚙️ Engine Switch**: Native segmented control to toggle between `⚡ Gemini API` (instant) and `📚 NotebookLM` (source-grounded synthesis).
- **👁️ Push from Preview**: Instantly detects and sends your open document in Preview.
- **📋 Push from Clipboard**: Instantly pushes whatever URL or text you copied (`Cmd+C`).

To have it automatically run in the background every time your Mac logs in:
```bash
uv run quadctl install-service
```

---

### 5. 🧠 Dual-Engine Summarizer & E-Ink Briefs

Quaderno Companion offers two summarization engines tailored for different workflows:

- **⚡ Gemini API (`gemini_api`)**: Ultra-fast (~1.5–2s) structured JSON synthesis via Google Developer API. Perfect for real-time background active-tab pushes and instant reading briefs.
- **📚 Gemini Notebook (`gemini_notebook` / NotebookLM RPC)**: Deep synthesis with strict numeric source citations (`[1]`, `[2]`) grounded directly in source documents via direct asynchronous RPCs. Perfect for research papers, long-form articles, and multi-document synthesis.

#### Usage & Commands:

```bash
# 1. Direct Gemini API summary (Fast ~2s, requires GEMINI_API_KEY):
uv run quadctl summarize https://arxiv.org/abs/2312.00752 --pages 2 --provider gemini_api

# 2. Gemini Notebook / NotebookLM summary (Deep Grounding with citations):
# Authenticate once with Google:
uv run quadctl notebook login
uv run quadctl notebook status

# Ephemeral Mode (creates temporary notebook, ingests source, extracts brief, auto-cleans up):
uv run quadctl summarize https://arxiv.org/abs/2312.00752 --pages 2 --provider gemini_notebook

# Shared Mode (queries an existing research notebook by URL or ID):
uv run quadctl summarize "What are the core architecture milestones?" --notebook-url "https://notebooklm.google.com/notebook/..." --pages 3
```

#### Menu Bar GUI Toggle:
In the macOS Menu Bar app, use the inline segmented control directly beneath the `📝 Summary:` slider to toggle between engines in real-time with zero configuration needed:
```text
📝 Summary: [ Slider: 2 pgs ]
[ ⚡ Gemini API | 📚 NotebookLM ]
```

---

### 6. 🌐 Chrome & Chromium Extension (Manifest V3)
1. In Chrome/Brave/Arc/Edge, navigate to `chrome://extensions`.
2. Enable **Developer mode** (top right toggle).
3. Click **Load unpacked** and select the `triggers/chrome-extension/` directory (or unpack `dist/quaderno-chrome-extension-v*.zip`).
4. Click the Quaderno toolbar icon to send or summarize the active tab.

---

### 7. 🦊 Firefox Extension
1. In Firefox, navigate to `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on...**.
3. Select `manifest.json` inside the `triggers/firefox-extension/` directory (or unpack `dist/quaderno-firefox-extension-v*.zip`).
4. Now you can click the Quaderno icon in your toolbar or right-click any web page / link and select **"Send Page to Quaderno"** or **"Summarize & Send to Quaderno"**.

---

### 8. 🌐 Browser Bookmarklet (Universal 1-Click Push)
Create a new bookmark with this URL:

```javascript
javascript:(function(){fetch('http://localhost:5000/api/agent/push',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:window.location.href,title:document.title})}).then(r=>r.json()).then(d=>alert('Pushed to Quaderno: '+d.details.title)).catch(e=>alert('Error pushing to Quaderno: '+e));})();
```

---

### 9. ⚡ Raycast Script Commands
Symlink or copy the scripts in `triggers/raycast/` to your Raycast scripts directory:
- `quaderno-next.sh` (e.g. mapped to foot pedal or shortcut)
- `quaderno-prev.sh`
- `quaderno-push.sh`
- `quaderno-status.sh`

---

## REST API Endpoints

- `GET /api/device/status`: Returns device telemetry (battery, storage, connection route, reading state).
- `GET /api/viewer/status`: Returns current reading position and document info.
- `POST /api/sync`: Triggers an immediate bidirectional sync pass with local mirror folder.
- `GET /api/sync/status`: Returns sync engine running state and last sync results.
- `POST /api/documents/open`: Accepts multipart file upload or JSON payload `{"url_or_path": "...", "page": 1}`.
- `POST /api/viewer/page`: Accepts `{"action": "next" | "prev" | "goto" | "offset", "page": <int>}`.
- `POST /api/agent/push`: Ingestion webhook for browser extensions (`{"url": "...", "title": "..."}`).
- `POST /api/agent/chat`: Natural language intent dispatcher (`{"query": "..."}`).

---

## 📦 Packaging & Standalone App

Quaderno Companion can be compiled into a standalone macOS `.app` bundle, `.dmg` installer, and Python wheel:

```bash
# Build all release artifacts (Wheel, DMG, Extensions, and Checksums)
uv run python scripts/release.py
```

Generated outputs in `dist/`:
- **`Quaderno-Companion-v*.dmg`**: Standalone macOS installer containing `Quaderno Companion.app` (runs as a lightweight menu bar agent with zero Dock clutter).
- **`quaderno_companion-*.whl`**: Isolated Python wheel installable via `uv tool install dist/quaderno_companion-*.whl` or `pipx`.
- **`quaderno-chrome-extension-v*.zip`** & **`quaderno-firefox-extension-v*.zip`**: Ready-to-load browser companion packages.
- **`checksums.sha256`**: SHA256 integrity verification hashes.

---

## Testing

Run all unit tests:

```bash
uv run pytest
```
