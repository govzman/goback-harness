const byId = (id) => document.getElementById(id);
const feed = byId("feed");
const edits = new Map();
let connected = false;
let mounted = false;
let chosen = false;
let busy = false;
let agentMessage;

function status(text, ready = false) {
  const badge = byId("status");
  badge.textContent = text;
  badge.classList.toggle("ready", ready);
}

function controls() {
  byId("folder").disabled = !connected || mounted;
  byId("mode").disabled = mounted;
  byId("mount").disabled = !connected || !chosen || mounted;
  byId("connect").disabled = mounted;
  byId("disconnect").hidden = !mounted;
  byId("mount-path").hidden = !mounted;
  byId("message").disabled = !mounted || busy;
  byId("send").disabled = !mounted || busy;
  byId("cancel").disabled = !mounted || !busy;
  for (const button of edits.values()) button.disabled = !mounted || busy;
}

function entry(label, content, className = "") {
  byId("empty")?.remove();
  const article = document.createElement("article");
  article.className = "entry " + className;
  const heading = document.createElement("h3");
  heading.textContent = label;
  const paragraph = document.createElement("p");
  paragraph.textContent = content;
  article.append(heading, paragraph);
  feed.append(article);
  feed.scrollTop = feed.scrollHeight;
  return { article, paragraph };
}

async function action(callback) {
  byId("error").hidden = true;
  try {
    return await callback();
  } catch (error) {
    byId("error").textContent = error.message.replace(/^Error invoking remote method '[^']+': Error: /, "");
    byId("error").hidden = false;
  } finally {
    controls();
  }
}

byId("connection").addEventListener("submit", (event) => {
  event.preventDefault();
  void action(async () => {
    connected = false;
    controls();
    await window.goback.connect(byId("server").value, byId("token").value);
    connected = true;
    status("Сервер подключён", true);
  });
});

byId("folder").addEventListener("click", () => void action(async () => {
  const path = await window.goback.pickFolder();
  if (path) {
    chosen = true;
    byId("folder-path").textContent = path;
  }
}));

byId("mount").addEventListener("click", () => void action(async () => {
  byId("mount").disabled = true;
  await window.goback.mount(byId("mode").value);
  mounted = true;
  status("Папка подключена", true);
}));

byId("disconnect").addEventListener("click", () => void action(async () => {
  await window.goback.disconnect();
  connected = false;
  mounted = false;
  busy = false;
  agentMessage = undefined;
  edits.clear();
  entry("GoBack", "Сессия закрыта.", "notice");
  status("Отключено");
}));

byId("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const composer = byId("message");
  const text = composer.value.trim();
  if (!text || busy) return;
  void action(async () => {
    await window.goback.send(text);
    composer.value = "";
  });
});

byId("cancel").addEventListener("click", () => void action(() => window.goback.cancel()));

function undoButton(container, id) {
  const button = document.createElement("button");
  button.className = "secondary";
  button.textContent = "Отменить правку";
  button.addEventListener("click", () => void action(async () => {
    button.disabled = true;
    await window.goback.undo(id);
  }));
  container.append(button);
  edits.set(id, button);
}

function undoReason(event) {
  const reason = event.reason || event.status;
  if (reason === "field_changed" && Object.hasOwn(event, "current")) {
    return `Ожидалось ${JSON.stringify(event.expected)}, в файле найдено ${JSON.stringify(event.current)}. Возврат к ${JSON.stringify(event.restore)} пропущен.`;
  }
  const messages = {
    file_missing: "Файл больше не найден.",
    file_too_large: "Размер файла превышает лимит операции.",
    field_missing: "Поля по этому пути больше нет.",
    invalid_json: "GoBack не смог разобрать текущий JSON.",
    field_not_scalar: "Поле теперь содержит объект или массив.",
    file_changed_during_operation: "Файл изменился во время отмены.",
    workdir_disconnected: "Связь с рабочей папкой прервана.",
    read_only: "Папка открыта только для чтения.",
    timeout: "Время ожидания отмены истекло. Запись не подтверждена.",
    transport_error: "Связь с сандбоксом прервалась. Запись не подтверждена.",
    protocol_error: "Ответ сандбокса не удалось проверить. Запись не подтверждена."
  };
  return messages[reason] || event.detail || `Точная причина недоступна · ${event.status || "conflict"}`;
}

window.goback.onEvent((event) => {
  switch (event.type) {
    case "workdir_mounted":
      entry("GoBack", `Подключена папка /work · ${event.mode === "rw" ? "чтение и запись" : "только чтение"}`, "notice");
      break;
    case "user_message":
      agentMessage = undefined;
      entry("Вы", event.text, "user");
      break;
    case "agent_delta":
      if (!agentMessage) agentMessage = entry("Агент", "").paragraph;
      agentMessage.textContent += event.text;
      break;
    case "file_changed":
      agentMessage = undefined;
      entry("GoBack", `Обнаружена внешняя правка в ${event.path}.`, "notice");
      break;
    case "edit_applied": {
      agentMessage = undefined;
      const field = event.address.join(".");
      const { article } = entry("Правка агента", `${event.path} · ${field}\n${JSON.stringify(event.old)} → ${JSON.stringify(event.new)}`, "notice");
      undoButton(article, event.edit_id);
      break;
    }
    case "undo_applied": {
      const button = edits.get(event.edit_id);
      if (button) {
        button.textContent = "Правка отменена";
        button.disabled = true;
        edits.delete(event.edit_id);
      }
      entry("GoBack", `Правка отменена · ${event.path}\n${event.address.join(".")} возвращено к ${JSON.stringify(event.to_value)}. Остальные поля сохранены.`, "notice");
      break;
    }
    case "undo_conflict":
      entry("Конфликт отмены", `${event.path} · ${event.address.join(".")}\n${undoReason(event)}\nФайл оставлен в текущем состоянии.`, "notice warning");
      break;
    case "undo_failed":
      entry("Ошибка отмены", `${event.path} · ${event.address.join(".")}\n${undoReason(event)}`, "notice warning");
      break;
    case "run_started":
      agentMessage = undefined;
      busy = true;
      status("Агент работает", true);
      break;
    case "run_error":
      entry("GoBack", event.detail, "notice warning");
      break;
    case "run_finished":
      busy = false;
      agentMessage = undefined;
      status(event.status === "cancelled" ? "Запуск остановлен" : "Папка подключена", true);
      break;
    case "stream_up":
      status("Связь восстановлена", true);
      break;
    case "stream_down":
      status("Подключаемся снова…");
      break;
    case "workdir_disconnected":
      entry("GoBack", "Связь с рабочей папкой прервана. Закрой сессию и подключи папку снова.", "notice warning");
      status("Папка недоступна");
      break;
    case "stream_ended":
    case "bridge_ended":
      mounted = false;
      connected = false;
      busy = false;
      status("Сессия завершилась");
      break;
  }
  controls();
  feed.scrollTop = feed.scrollHeight;
});

controls();
