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
}
