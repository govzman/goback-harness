// Exercise the actual desktop bridge and chat UI with a private demonstration folder.
const { app, dialog } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { setTimeout: delay } = require("node:timers/promises");

const folder = process.env.GOBACK_ELECTRON_TEST_FOLDER;
const address = process.env.GOBACK_TEST_SERVER || "http://127.0.0.1:8080";
assert(folder, "Set GOBACK_ELECTRON_TEST_FOLDER to a dedicated empty test directory");
fs.mkdirSync(folder, { recursive: true });
const config = path.join(folder, "config.json");
fs.writeFileSync(config, JSON.stringify({ port: 8080, workers: 2 }, null, 2));
dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] });

async function waitFor(window, expression) {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    if (await window.webContents.executeJavaScript(expression)) return;
    await delay(100);
  }
  const error = await window.webContents.executeJavaScript("document.querySelector('#error').textContent");
  throw new Error(`Timed out waiting for ${expression}. ${error}`);
}

async function exercise(window) {
  const script = (source) => window.webContents.executeJavaScript(source);
  await script(`document.querySelector('#server').value = ${JSON.stringify(address)};
    document.querySelector('#token').value = 'goback-local-demo'; document.querySelector('#connect').click()`);
  await waitFor(window, "!document.querySelector('#folder').disabled");
  await script("document.querySelector('#folder').click(); document.querySelector('#mode').value = 'rw'");
  await waitFor(window, "!document.querySelector('#mount').disabled");
  await script("document.querySelector('#mount').click()");
  await waitFor(window, "!document.querySelector('#send').disabled");
  await script("document.querySelector('#message').value = 'Поставь порт 9090 в config.json'; document.querySelector('#send').click()");
  await waitFor(window, "document.querySelector('.entry button') && !document.querySelector('.entry button').disabled");
  assert.equal(JSON.parse(fs.readFileSync(config)).port, 9090);
  fs.writeFileSync(config, JSON.stringify({ port: 9090, workers: 3 }, null, 2));
  await script("document.querySelector('.entry button').click()");
  await waitFor(window, "document.querySelector('#feed').textContent.includes('Остальные поля сохранены.')");
  assert.deepEqual(JSON.parse(fs.readFileSync(config)), { port: 8080, workers: 3 });
  assert(await script("document.querySelector('#feed').textContent.includes('Обнаружена внешняя правка')"));
  if (process.env.GOBACK_SCREENSHOT) {
    const image = await window.webContents.capturePage();
    fs.writeFileSync(process.env.GOBACK_SCREENSHOT, image.toPNG());
  }
  await script("document.querySelector('#disconnect').click()");
  await waitFor(window, "document.querySelector('#status').textContent === 'Отключено'");
  console.log("Electron mount, chat, live workspace edit and selective undo passed.");
}

app.on("browser-window-created", (_event, window) => {
  window.webContents.once("did-finish-load", () => {
    exercise(window).then(() => app.exit(0), (error) => {
      console.error(error);
      app.exit(1);
    });
  });
});

require("../../client/desktop/main.cjs");
