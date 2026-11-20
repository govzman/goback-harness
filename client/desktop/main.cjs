const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const { randomUUID } = require("node:crypto");
const path = require("node:path");

let window;
let bridge;
let folder;
const pending = new Map();

function startBridge() {
  const python = process.env.GOBACK_PYTHON || (process.platform === "win32" ? "python.exe" : "python3");
  const cli = path.join(__dirname, "..", "cli.py");
  const child = spawn(python, ["-u", cli, "bridge"], { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  bridge = child;
  child.stderr.resume();
  const lines = createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    const event = JSON.parse(line);
    if (event.type === "reply") {
      const request = pending.get(event.id);
      pending.delete(event.id);
      if (request) {
        clearTimeout(request.timer);
        if (event.ok) request.resolve(event);
        else request.reject(new Error(event.error));
      }
      return;
    }
    if (window && !window.isDestroyed()) window.webContents.send("goback:event", event);
  });
  const close = () => {
    if (bridge !== child) return;
    bridge = undefined;
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error("Python-клиент остановился. Проверь путь и зависимости."));
    }
    pending.clear();
    if (window && !window.isDestroyed()) window.webContents.send("goback:event", { type: "bridge_ended" });
  };
  child.on("error", close);
  child.on("exit", close);
  child.stdin.on("error", close);
}

function send(action, values = {}) {
  if (!bridge) startBridge();
  const id = randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("Сервер не ответил вовремя."));
    }, 70_000);
    pending.set(id, { resolve, reject, timer });
    bridge.stdin.write(JSON.stringify({ id, action, ...values }) + "\n");
  });
}

function checkCaller(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) {
    throw new Error("Unexpected IPC sender");
  }
}

ipcMain.handle("goback:folder", async (event) => {
  checkCaller(event);
  const result = await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
  if (!result.canceled) folder = result.filePaths[0];
  return result.canceled ? null : folder;
});
ipcMain.handle("goback:connect", (event, server, token) => {
  checkCaller(event);
  return send("connect", { server, token });
});
ipcMain.handle("goback:mount", (event, mode) => {
  checkCaller(event);
  if (!folder) throw new Error("Выбери папку.");
  if (!["ro", "rw"].includes(mode)) throw new Error("Выбери режим доступа.");
  return send("mount", { folder, mode });
});
ipcMain.handle("goback:send", (event, text) => {
  checkCaller(event);
  return send("send", { text });
});
ipcMain.handle("goback:undo", (event, edit_id) => {
  checkCaller(event);
  return send("undo", { edit_id });
});
ipcMain.handle("goback:cancel", (event) => {
  checkCaller(event);
  return send("cancel");
});
ipcMain.handle("goback:disconnect", (event) => {
  checkCaller(event);
  return send("disconnect");
});

app.whenReady().then(() => {
  window = new BrowserWindow({
    title: "GoBack Harness",
    width: 1140,
    height: 760,
    minWidth: 840,
    minHeight: 560,
    backgroundColor: "#f6f7f4",
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, sandbox: true }
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.loadFile(path.join(__dirname, "index.html"));
});

app.on("window-all-closed", () => {
  if (!bridge) return app.quit();
  const child = bridge;
  child.once("exit", () => app.quit());
  child.stdin.end();
  setTimeout(() => {
    if (bridge === child) child.kill();
    app.quit();
  }, 4000).unref();
});
