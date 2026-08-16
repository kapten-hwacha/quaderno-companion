async function getCurrentTab() {
  const tabs = await browser.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function sendToQuaderno(summarize) {
  const statusDiv = document.getElementById("status");
  statusDiv.innerText = "Sending to Quaderno...";

  try {
    const tab = await getCurrentTab();
    const isPdf = tab.url.toLowerCase().includes(".pdf");

    // If PDF tab and not summarizing, use browser session to download bypass WAF/SSO
    if (isPdf && !summarize) {
      statusDiv.innerText = "Transferring PDF from browser...";
      try {
        const response = await fetch(tab.url, { credentials: "include" });
        if (response.ok) {
          const blob = await response.blob();
          const formData = new FormData();
          const cleanTitle = (tab.title || "document").replace(/[^a-zA-Z0-9_\-\. ]/g, "_");
          const filename = cleanTitle.endsWith(".pdf") ? cleanTitle : cleanTitle + ".pdf";
          formData.append("file", blob, filename);
          formData.append("title", cleanTitle);

          const res = await fetch("http://localhost:5000/api/documents/open", {
            method: "POST",
            body: formData
          });
          if (res.ok) {
            statusDiv.innerText = "✓ Sent to Quaderno!";
            setTimeout(() => window.close(), 1200);
            return;
          }
        }
      } catch (e) {
        console.warn("Direct blob fetch fallback to daemon URL fetch", e);
      }
    }

    const res = await fetch("http://localhost:5000/api/agent/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: tab.url,
        title: tab.title,
        summarize: summarize
      })
    });

    if (!res.ok) throw new Error("HTTP " + res.status);
    statusDiv.innerText = "✓ Sent to Quaderno!";
    setTimeout(() => window.close(), 1200);
  } catch (err) {
    statusDiv.innerText = "✗ Failed to send to Quaderno.";
  }
}

document.getElementById("btn-push").addEventListener("click", () => sendToQuaderno(false));
document.getElementById("btn-sum").addEventListener("click", () => sendToQuaderno(true));

// Initial status check
fetch("http://localhost:5000/api/device/status")
  .then(r => r.json())
  .then(d => {
    const statusDiv = document.getElementById("status");
    if (d.is_connected) {
      statusDiv.innerText = `Connected (${d.connection_type}) • ${d.battery_level || 0}%`;
    } else {
      statusDiv.innerText = "Daemon running (Device offline)";
    }
  })
  .catch(() => {
    document.getElementById("status").innerText = "Daemon offline (Run `quadctl serve` or `quadctl app`)";
  });
