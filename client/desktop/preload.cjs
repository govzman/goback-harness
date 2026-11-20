const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("goback", {
  pickFolder: () => ipcRenderer.invoke("goback:folder"),
  connect: (server, token) => ipcRenderer.invoke("goback:connect", server, token),
  mount: (mode) => ipcRenderer.invoke("goback:mount", mode),
  send: (text) => ipcRenderer.invoke("goback:send", text),
  undo: (id) => ipcRenderer.invoke("goback:undo", id),
  cancel: () => ipcRenderer.invoke("goback:cancel"),
  disconnect: () => ipcRenderer.invoke("goback:disconnect"),
  onEvent: (callback) => ipcRenderer.on("goback:event", (_event, data) => callback(data))
});
