const { test } = require('node:test');
const assert = require('node:assert/strict');
const { isAppSender, isAppUrl, isExternalUrl, isHexColor, isLauncherSender } = require('../security.cjs');

test('only the local MPP origin can navigate inside the application', () => {
  assert.equal(isAppUrl('http://localhost:18000/files', 'http://localhost:18000'), true);
  for (const url of ['http://localhost:18001', 'http://localhost.evil:18000', 'file:///C:/config.json', 'javascript:alert(1)', 'https://example.com']) {
    assert.equal(isAppUrl(url, 'http://localhost:18000'), false);
  }
});

test('external opening is limited to http(s) URLs without credentials', () => {
  assert.equal(isExternalUrl('https://example.com/video'), true);
  for (const url of ['file:///C:/Windows', 'javascript:alert(1)', 'ms-settings:', 'https://user:secret@example.com', 'broken']) {
    assert.equal(isExternalUrl(url), false);
  }
});

test('desktop IPC accepts only the launcher main frame in our window', () => {
  const launcher = 'file:///desktop/launcher/index.html';
  const frame = { url: launcher };
  const contents = { mainFrame: frame };
  const window = { isDestroyed: () => false, webContents: contents };
  const event = { sender: contents, senderFrame: frame };
  assert.equal(isLauncherSender(event, window, launcher), true);
  assert.equal(isLauncherSender({ ...event, senderFrame: { url: launcher } }, window, launcher), false);
  frame.url = 'http://localhost:18000';
  assert.equal(isLauncherSender(event, window, launcher), false);
});

test('the page bridge answers only the MPP page in our main frame', () => {
  const server = 'http://127.0.0.1:18000';
  const frame = { url: `${server}/#/files` };
  const contents = { mainFrame: frame };
  const window = { isDestroyed: () => false, webContents: contents };
  const event = { sender: contents, senderFrame: frame };
  assert.equal(isAppSender(event, window, server), true);
  assert.equal(isAppSender({ ...event, senderFrame: { url: frame.url } }, window, server), false);
  assert.equal(isAppSender({ ...event, sender: {} }, window, server), false);
  frame.url = 'file:///desktop/launcher/index.html';
  assert.equal(isAppSender(event, window, server), false);
  frame.url = 'https://example.com';
  assert.equal(isAppSender(event, window, server), false);
  assert.equal(isAppSender(event, { ...window, isDestroyed: () => true }, server), false);
});

test('title bar colours must be plain hex', () => {
  assert.equal(isHexColor('#fafafa'), true);
  for (const value of ['red', '#fff', 'rgb(0,0,0)', '#12345g', null, undefined, 42]) {
    assert.equal(isHexColor(value), false);
  }
});
