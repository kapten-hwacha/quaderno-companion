# Quaderno Companion 📖✨

An autonomous background agent and zero-friction desktop bridge for the **Fujitsu Quaderno Gen 2** (A4 & A5 models) over local network protocols (Wi-Fi, Bluetooth PAN, and USB).

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
│ • Readability / Boilerplate   │ │   (Wi-Fi ➜ BT PAN ➜ USB)  │
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
- **Ultra-Fast Wireless Push**: Deflates and compresses PDF streams down to <300 KB for near-instant Bluetooth PAN transfer.
- **Network Auto-Routing**: Automatically discovers and fails over across Wi-Fi (`digitalpaper.local` or static IP), Bluetooth PAN (`bnep0`/`en*`), and USB tethering (`172.25.47.1`).
- **Reading Navigation Controller**: Changes pages (`next`, `prev`, `goto <n>`) programmatically without re-uploading documents.
- **Autonomous Agent & Summarizer**: Synthesizes structured 1-page executive briefs from text/URLs directly into high-contrast E-ink layouts.
- **1-Click Triggers**: Browser bookmarklet, Chrome extension (Manifest V3), and Raycast script commands for desktop workflows.

---

## Quickstart

### 1. Installation

This project is managed with [`uv`](https://github.com/astral-sh/uv).

```bash
# Clone the repository
git clone https://github.com/your-username/quaderno-companion.git
cd quaderno-companion

# Sync virtualenv and dependencies
uv sync
```

### 2. Device Pairing (One-Time Setup)

On your Fujitsu Quaderno:
1. Go to **Settings** ➜ **Wi-Fi** / **Device Configuration** and locate the **Pairing PIN** (or enable pairing mode).
2. Run the pairing command:

```bash
uv run quadctl pair
```

Credentials (`deviceid.dat` and `key.pem`) will be securely saved to `~/.config/quaderno/`.

### 3. Start the Background Daemon

```bash
uv run quadctl serve
```

The daemon will use port 5000.

---

## CLI Usage (`quadctl`)

| Command | Description |
|---|---|
| `uv run quadctl app` | Launches native macOS Menu Bar background app |
| `uv run quadctl sync` | Runs an immediate bidirectional sync pass with `~/Quaderno` |
| `uv run quadctl open` | Opens the local Quaderno mirror folder in macOS Finder |
| `uv run quadctl push [url_or_path]` | Auto-detects active browser tab (Firefox, Safari, Chrome) or pushes URL/file |
| `uv run quadctl window` | Captures the currently active macOS window and pushes it to Quaderno |
| `uv run quadctl preview` | Pushes/mirrors the document active in Apple Preview |
| `uv run quadctl preview --watch` | Continuous real-time page mirror with Apple Preview |
| `uv run quadctl install-service` | Installs background auto-start daemon (macOS LaunchAgent) |
| `uv run quadctl uninstall-service` | Removes background macOS LaunchAgent |
| `uv run quadctl serve` | Starts the FastAPI daemon |
| `uv run quadctl next` | Advances to the next page on the Quaderno |
| `uv run quadctl prev` | Returns to the previous page on the Quaderno |
| `uv run quadctl goto <page>` | Jumps to a specific page number |
| `uv run quadctl status` | Shows connection status, battery, storage, and active page |
| `uv run quadctl summarize <url>` | Generates a 1-page E-ink brief and pushes to display |
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
- **📝 Summarize Mode**: Checkbox toggle to synthesize 1-page E-ink executive briefs.
- **👁️ Push from Preview**: Instantly detects and sends your open document in Preview.
- **📋 Push from Clipboard**: Instantly pushes whatever URL or text you copied (`Cmd+C`).

To have it automatically run in the background every time your Mac logs in:
```bash
uv run quadctl install-service
```

---

### 2. 🦊 Firefox Extension
1. In Firefox, navigate to `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on...**.
3. Select `manifest.json` inside the `triggers/firefox-extension/` directory.
4. Now you can click the Quaderno icon in your toolbar or right-click any web page / link and select **"Send Page to Quaderno"** or **"Summarize & Send to Quaderno"**.

---

### 3. 🌐 Browser Bookmarklet (Universal 1-Click Push)
Create a new bookmark with this URL:

```javascript
javascript:(function(){fetch('http://localhost:5000/api/agent/push',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:window.location.href,title:document.title})}).then(r=>r.json()).then(d=>alert('Pushed to Quaderno: '+d.details.title)).catch(e=>alert('Error pushing to Quaderno: '+e));})();
```

---

### 4. ⚡ Raycast Script Commands
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

## Testing

Run all unit tests:

```bash
uv run pytest
```
