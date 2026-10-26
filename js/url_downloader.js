// URL からモデル・カスタムノードをダウンロードするサイドバーパネル
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SETTING_CIVITAI = "UrlDownloader.CivitaiToken";
const SETTING_HF = "UrlDownloader.HuggingFaceToken";
const POLL_OPEN_MS = 1000;
const POLL_BACKGROUND_MS = 5000;

const STATUS_LABEL = {
  queued: "待機中",
  downloading: "ダウンロード中",
  done: "完了",
  failed: "失敗",
  canceled: "中止",
};
const ACTIVE = new Set(["queued", "downloading"]);

let jobs = [];
let modelTypes = [];
let customNodesDir = null;
let panelEl = null;
let pollTimer = null;

function getSetting(id) {
  try {
    return app.extensionManager?.setting?.get(id) || "";
  } catch {
    return "";
  }
}

// idle_disconnect との連携：待機中・ダウンロード中のジョブがある間は自動切断を止めてもらう
function publishBusy() {
  window.__torilabBusy = window.__torilabBusy || {};
  window.__torilabBusy.urlDownloader = jobs.some((j) => ACTIVE.has(j.status));
}

async function request(path, options) {
  const r = await api.fetchApi(path, options);
  let body = null;
  try {
    body = await r.json();
  } catch {}
  if (!r.ok) throw new Error(body?.error || `HTTP ${r.status}`);
  return body;
}

function formatBytes(n) {
  if (n == null) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

async function refreshJobs() {
  try {
    jobs = (await request("/url_downloader/jobs")).jobs || [];
  } catch (err) {
    console.warn("[UrlDownloader] jobs の取得に失敗", err);
  }
  publishBusy();
  renderJobs();
  schedulePoll();
}

// パネルを開いている間は 1 秒ごと、閉じていても実行中のジョブがあれば 5 秒ごとに更新する
function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = null;
  const open = panelEl && panelEl.isConnected;
  const active = jobs.some((j) => ACTIVE.has(j.status));
  if (open || active) pollTimer = setTimeout(refreshJobs, open ? POLL_OPEN_MS : POLL_BACKGROUND_MS);
}

// ---- 描画 ----

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "style") Object.assign(e.style, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) e.append(c);
  return e;
}

const S = {
  section: { display: "grid", gap: "6px", marginBottom: "12px" },
  label: { fontSize: "12px", opacity: "0.8" },
  input: {
    width: "100%", boxSizing: "border-box", padding: "4px 6px", font: "inherit",
    background: "var(--comfy-input-bg)", color: "var(--input-text)", border: "1px solid var(--border-color)", borderRadius: "4px",
  },
  button: {
    padding: "4px 12px", font: "inherit", cursor: "pointer", borderRadius: "4px",
    background: "var(--comfy-input-bg)", color: "var(--input-text)", border: "1px solid var(--border-color)",
  },
  job: { border: "1px solid var(--border-color)", borderRadius: "4px", padding: "6px", marginBottom: "6px", fontSize: "12px", wordBreak: "break-all" },
  bar: { height: "4px", background: "var(--border-color)", borderRadius: "2px", overflow: "hidden", margin: "4px 0" },
};

function renderForm(container) {
  const kind = el("select", { style: S.input },
    el("option", { value: "model" }, "モデル"),
    el("option", { value: "custom_node" }, "カスタムノード"));
  const url = el("input", { type: "url", placeholder: "https://...", style: S.input });
  const modelType = el("select", { style: S.input },
    modelTypes.map((t) => el("option", { value: t.name, selected: t.name === "checkpoints" }, t.name)));
  const subfolder = el("input", { type: "text", placeholder: "例: sdxl/style（任意）", style: S.input });
  const filename = el("input", { type: "text", placeholder: "空なら自動で決める（任意）", style: S.input });
  const destView = el("div", { style: { fontSize: "11px", opacity: "0.7", wordBreak: "break-all" } });
  const error = el("div", { style: { color: "var(--error-text, #f66)", fontSize: "12px" } });
  const modelFields = el("div", { style: S.section },
    el("label", { style: S.label }, "モデルの種類"), modelType,
    el("label", { style: S.label }, "サブフォルダ"), subfolder,
    el("label", { style: S.label }, "ファイル名"), filename);
  const submit = el("button", { type: "button", style: S.button }, "ダウンロード");

  const updateView = () => {
    const isModel = kind.value === "model";
    modelFields.style.display = isModel ? "grid" : "none";
    if (isModel) {
      const base = modelTypes.find((t) => t.name === modelType.value)?.path || "?";
      const sub = subfolder.value.trim();
      destView.textContent = `保存先: ${base}${sub ? "/" + sub : ""}/${filename.value.trim() || "（自動）"}`;
    } else {
      destView.textContent = customNodesDir
        ? `保存先: ${customNodesDir}/<リポジトリ名>`
        : "custom_nodes_dev が見つかりません（カスタムノードは入れられません）";
    }
  };
  for (const e of [kind, modelType]) e.addEventListener("change", updateView);
  for (const e of [subfolder, filename]) e.addEventListener("input", updateView);

  submit.addEventListener("click", async () => {
    error.textContent = "";
    submit.disabled = true;
    try {
      const body = { kind: kind.value, url: url.value.trim() };
      if (kind.value === "model") {
        Object.assign(body, {
          model_type: modelType.value,
          subfolder: subfolder.value.trim(),
          filename: filename.value.trim(),
          civitai_token: getSetting(SETTING_CIVITAI),
          hf_token: getSetting(SETTING_HF),
        });
      }
      await request("/url_downloader/jobs", { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
      url.value = "";
      filename.value = "";
      updateView();
      await refreshJobs();
    } catch (err) {
      error.textContent = String(err.message || err);
    } finally {
      submit.disabled = false;
    }
  });

  updateView();
  container.append(el("div", { style: S.section },
    el("label", { style: S.label }, "区分"), kind,
    el("label", { style: S.label }, "URL"), url),
    modelFields, destView, el("div", { style: { margin: "8px 0" } }, submit), error);
}

function renderJobs() {
  const list = panelEl?.querySelector("[data-jobs]");
  if (!list) return;
  list.replaceChildren();
  if (!jobs.length) {
    list.append(el("div", { style: { fontSize: "12px", opacity: "0.6" } }, "ジョブはありません"));
    return;
  }
  for (const j of jobs) {
    const pct = j.total ? Math.min(100, (j.received / j.total) * 100) : null;
    const color = j.status === "failed" ? "#e55" : j.status === "done" ? "#5b5" : "var(--p-primary-color, #48f)";
    const actions = [];
    if (ACTIVE.has(j.status)) {
      actions.push(el("button", { type: "button", style: S.button, onclick: () => request(`/url_downloader/jobs/${j.id}/cancel`, { method: "POST" }).then(refreshJobs) }, "中止"));
    } else {
      actions.push(el("button", { type: "button", style: S.button, onclick: () => request(`/url_downloader/jobs/${j.id}`, { method: "DELETE" }).then(refreshJobs) }, "一覧から消す"));
    }
    list.append(el("div", { style: S.job },
      el("div", { style: { fontWeight: "bold" } }, `${STATUS_LABEL[j.status] || j.status}（${j.kind === "model" ? "モデル" : "カスタムノード"}）`),
      el("div", { style: { opacity: "0.8" } }, j.url),
      j.dest ? el("div", { style: { opacity: "0.7" } }, `→ ${j.dest}`) : null,
      j.kind === "model" && (j.received || j.status === "downloading")
        ? [
            el("div", { style: S.bar }, el("div", { style: { height: "100%", width: `${pct ?? 0}%`, background: color } })),
            el("div", {}, `${formatBytes(j.received)} / ${formatBytes(j.total)}${pct != null ? `（${pct.toFixed(1)}%）` : ""}${j.status === "downloading" ? ` ${formatBytes(j.speed)}/s` : ""}`),
          ]
        : null,
      j.message ? el("div", { style: { color: j.status === "failed" ? "#e55" : "inherit" } }, j.message) : null,
      j.warning ? el("div", { style: { color: "#d90" } }, j.warning) : null,
      el("div", { style: { marginTop: "4px" } }, actions)));
  }
}

async function renderPanel(container) {
  panelEl = el("div", { style: { padding: "12px", overflowY: "auto", height: "100%", boxSizing: "border-box" } });
  container.replaceChildren(panelEl);
  panelEl.append(el("h3", { style: { margin: "0 0 12px", fontSize: "14px" } }, "URL ダウンロード"));
  try {
    const info = await request("/url_downloader/model_types");
    modelTypes = info.model_types || [];
    customNodesDir = info.custom_nodes_dir;
  } catch (err) {
    panelEl.append(el("div", { style: { color: "#e55" } }, `モデルの種類を取得できません: ${err.message}`));
  }
  const form = el("div");
  renderForm(form);
  panelEl.append(form, el("h4", { style: { margin: "16px 0 8px", fontSize: "13px" } }, "ジョブ"), el("div", { "data-jobs": true }));
  await refreshJobs();
}

app.registerExtension({
  name: "torilab.UrlDownloader",
  settings: [
    {
      id: SETTING_CIVITAI,
      category: ["UrlDownloader", "トークン", "CivitAI"],
      name: "CivitAI の API トークン",
      tooltip: "ログインが必要なモデルのダウンロードに使う。user/ 配下に平文で保存される",
      type: "password",
      defaultValue: "",
    },
    {
      id: SETTING_HF,
      category: ["UrlDownloader", "トークン", "HuggingFace"],
      name: "Hugging Face のトークン",
      tooltip: "認証が必要なモデルのダウンロードに使う。user/ 配下に平文で保存される",
      type: "password",
      defaultValue: "",
    },
  ],
  setup() {
    try {
      app.extensionManager.registerSidebarTab({
        id: "torilab-url-downloader",
        icon: "pi pi-download",
        title: "URL ダウンロード",
        tooltip: "URL からモデル・カスタムノードをダウンロード",
        type: "custom",
        render: (container) => {
          renderPanel(container).catch((err) => console.warn("[UrlDownloader] render failed", err));
        },
        destroy: () => {
          panelEl = null;
          schedulePoll();
        },
      });
      // 画面を読み込み直す前から動いているジョブがあれば拾う
      refreshJobs();
    } catch (err) {
      console.warn("[UrlDownloader] setup failed", err);
    }
  },
});
