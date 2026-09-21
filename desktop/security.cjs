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

module.exports = { isAppUrl, isExternalUrl, isLauncherSender };
