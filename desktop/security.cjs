function isAppUrl(value, base) {
  try { return new URL(value).origin === new URL(base).origin; } catch { return false; }
}

function isExternalUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
  } catch { return false; }
}

function isLauncherSender(event, window, launcherUrl) {
  return !window.isDestroyed() && event.sender === window.webContents
    && event.senderFrame === window.webContents.mainFrame
    && event.senderFrame.url === launcherUrl;
}

// The MPP page itself (served by the backend), in our window's main frame.
function isAppSender(event, window, serverUrl) {
  return !window.isDestroyed() && event.sender === window.webContents
    && event.senderFrame === window.webContents.mainFrame
    && isAppUrl(event.senderFrame.url, serverUrl);
}

// Window button overlay colours come from the page; only plain #rrggbb is passed on.
function isHexColor(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
}

module.exports = { isAppSender, isAppUrl, isExternalUrl, isHexColor, isLauncherSender };
