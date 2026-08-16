async function getCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function sendToQuaderno(summarize) {
  const statusDiv = document.getElementById("status");
  statusDiv.innerText = "Sending to Quaderno...";

  try {
    const tab = await getCurrentTab();
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
    const data = await res.json();
    statusDiv.innerText = "✓ Sent to Quaderno!";
    setTimeout(() => window.close(), 1200);
  } catch (err) {
    statusDiv.innerText = "✗ Failed to connect to quadctl daemon.";
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
    document.getElementById("status").innerText = "Daemon offline (Run `quadctl serve`)";
  });
