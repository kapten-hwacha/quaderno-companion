/**
 * Quaderno Companion - Firefox WebExtension Background Script
 * Adds Right-Click Context Menu items for sending links and pages to Quaderno.
 */

browser.runtime.onInstalled.addListener(() => {
  browser.contextMenus.create({
    id: "quaderno-push-page",
    title: "Send Page to Quaderno",
    contexts: ["page"]
  });

  browser.contextMenus.create({
    id: "quaderno-push-link",
    title: "Send Link to Quaderno",
    contexts: ["link"]
  });

  browser.contextMenus.create({
    id: "quaderno-summarize-page",
    title: "Summarize & Send to Quaderno",
    contexts: ["page"]
  });
});

browser.contextMenus.onClicked.addListener(async (info, tab) => {
  let targetUrl = info.linkUrl || info.pageUrl || (tab ? tab.url : null);
  let title = tab ? tab.title : "Web Document";
  let summarize = info.menuItemId === "quaderno-summarize-page";

  if (!targetUrl) return;

  try {
    const res = await fetch("http://localhost:5000/api/agent/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: targetUrl,
        title: title,
        summarize: summarize
      })
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    console.log("Pushed successfully to Quaderno daemon.");
  } catch (err) {
    console.error("Failed to push to Quaderno daemon:", err);
  }
});
