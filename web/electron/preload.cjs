const { contextBridge, ipcRenderer } = require("electron")

function subscribe(channel, callback) {
  const listener = (_event, payload) => callback(payload)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

contextBridge.exposeInMainWorld("mppBackend", {
  getStatus: () => ipcRenderer.invoke("mpp-backend:get-status"),
  getLogs: () => ipcRenderer.invoke("mpp-backend:get-logs"),
  start: () => ipcRenderer.invoke("mpp-backend:start"),
  stop: () => ipcRenderer.invoke("mpp-backend:stop"),
  restart: () => ipcRenderer.invoke("mpp-backend:restart"),
  onStatus: (callback) => subscribe("mpp-backend:status", callback),
  onLog: (callback) => subscribe("mpp-backend:log", callback),
})
