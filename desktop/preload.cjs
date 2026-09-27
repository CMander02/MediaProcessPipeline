const { contextBridge, ipcRenderer } = require('electron');

// The React app has the same browser privileges as the existing Web frontend.
// The file launcher alone receives a small, fixed set of desktop actions.
if (window.location.protocol === 'file:' && window.location.pathname.endsWith('/launcher/index.html')) {
  contextBridge.exposeInMainWorld('mppDesktop', {
    getState: () => ipcRenderer.invoke('desktop:state'),
    retry: () => ipcRenderer.invoke('desktop:action', 'retry'),
    chooseProject: () => ipcRenderer.invoke('desktop:action', 'choose-project'),
    openLogs: () => ipcRenderer.invoke('desktop:action', 'open-logs'),
    quit: () => ipcRenderer.invoke('desktop:action', 'quit'),
    onState: (callback) => {
      const listener = (_event, state) => callback(state);
      ipcRenderer.on('desktop:state', listener);
      return () => ipcRenderer.removeListener('desktop:state', listener);
    },
  });
} else if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
  // The MPP page gets the old native menu's actions and the title bar colours.
  // The main process only answers when the sender is the MPP page in our window.
  contextBridge.exposeInMainWorld('mppDesktopApp', {
    action: (name) => ipcRenderer.invoke('desktop:app-action', name),
    info: () => ipcRenderer.invoke('desktop:app-info'),
    setTitleBarColors: (colors) => ipcRenderer.invoke('desktop:title-bar', colors),
  });
}
