// Crash the first launcher load before it finishes. Later loads use a new renderer.
const { BrowserWindow } = require('electron');
const original = BrowserWindow.prototype.loadFile;
let firstLoad = true;
BrowserWindow.prototype.loadFile = function (...args) {
  if (firstLoad) {
    firstLoad = false;
    this.webContents.once('dom-ready', () => this.webContents.forcefullyCrashRenderer());
    return original.apply(this, args);
  }
  return original.apply(this, args).then((result) => {
    console.log('MPP_STARTUP_CRASH_RECOVERED');
    return result;
  });
};
require('../../main.cjs');
