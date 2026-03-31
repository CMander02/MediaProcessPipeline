// Open side panel when extension icon is clicked
chrome.action.onClicked.addListener((tab) => {
  if (tab.id) {
    chrome.sidePanel.open({ tabId: tab.id })
  }
})

// Relay messages from content scripts to side panel
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "VIDEO_DATA" || message.type === "VIDEO_CHANGED" || message.type === "NO_SUBTITLES") {
    // Forward to all extension pages (side panel will pick it up)
    chrome.runtime.sendMessage(message).catch(() => {
      // Side panel not open — ignore
    })
  }
  // Allow async response
  return false
})
