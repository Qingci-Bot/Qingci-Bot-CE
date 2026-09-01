"use strict";

/**
 * Qingci-Bot CE Electron 主进程
 *
 * 架构：Electron 作为桌面壳，spawn 一个 Python 后端进程（main.py --backend），
 * 窗口加载后端 HTTP 提供的 Web UI（http://127.0.0.1:<port>/ui）。前端通过
 * 同源相对路径 /api、/api/ws 访问后端，CORS/WebSocket 环回鉴权天然放行。
 *
 * 职责：
 *  - 实例解析：先以 --resolve-instance 探测实例元数据（data_dir/port/host）
 *  - 单实例：基于 data-dir 退化的控制端口实现 per-data-dir 单实例（同目录聚焦、
 *    不同 data-dir 可并行多开）
 *  - 生命周期：spawn Python 后端、解析 READY 信号、退出时回收子进程
 *  - 桌面能力：系统托盘、关闭驻留后台、真正的退出流程
 */

const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain } = require("electron");
const { spawn, spawnSync } = require("child_process");
const net = require("net");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const RS = "\u001E"; // Record Separator，与 main.py --backend 的 READY 信号分隔符一致

// ── 主进程日志（落盘到后端 data 目录 logs/electron.log，排障用） ──
let _logStream = null;
let _logRoot = null;
function _logInit(root) {
  if (!root || _logRoot === root) return;
  _logRoot = root;
  try {
    const dir = path.join(root, "logs");
    fs.mkdirSync(dir, { recursive: true });
    _logStream = fs.createWriteStream(path.join(dir, "electron.log"), { flags: "a", encoding: "utf8" });
  } catch (e) { /* 日志不可用不阻塞主流程 */ }
}
function elog(...parts) {
  const line = `[${new Date().toISOString()}] ${parts.map(p => (typeof p === "string" ? p : String(p && p.message || p))).join(" ")}`;
  if (_logStream) { try { _logStream.write(line + "\n"); } catch (_e) {} }
  if (isDev()) console.log(line);
}
function logHintPath() {
  const root = _logRoot || (instanceMeta && instanceMeta.data_dir) || app.getPath("userData");
  return path.join(root, "logs", "electron.log");
}

// ── 配置与路径 ────────────────────────────────────────────────
// 打包目录：desktop/electron/ 下；Python 产物由 build 脚本放置到 app 相邻的 backend/ 目录。
// 开发目录：desktop/electron/ 的上级两级为项目根（含 main.py / .venv）。
const APP_ROOT = path.resolve(__dirname, "..", "..");

function isDev() {
  return !app.isPackaged;
}

function resolvePython() {
  if (isDev()) {
    const winPy = path.join(APP_ROOT, ".venv", "Scripts", "python.exe");
    return { cmd: fs.existsSync(winPy) ? winPy : (process.platform === "win32" ? "python" : "python3"), script: path.join(APP_ROOT, "main.py") };
  }
  // 打包：后端 onedir 经 electron-builder extraResources 复制到 resources/backend/，
  // 与 exe 同目录的 _internal/ms-playwright/web/instances 随之整体保留。
  // PyInstaller 产物名跨平台：Windows 为 qingci-bot-ce.exe，Linux/macOS 无扩展名。
  const backendExe = path.join(process.resourcesPath, "backend", process.platform === "win32" ? "qingci-bot-ce.exe" : "qingci-bot-ce");
  return { cmd: backendExe, script: null };
}

// command-line args 传递给后端（排除 electron 自身 args 与由壳注入的 --backend）
function forwardArgs() {
  const start = process.defaultApp ? 2 : 1;
  const argv = process.argv.slice(start).filter((a) => a !== "--desktop" && a !== "--backend");
  return argv;
}

// 取 launch args 中 `--flag value`（空格分隔）的取值；`--instance=val` 形式不属于
// 后端 _build_start_args 派发约定，此处不处理。未找到返回 null。
function argValue(argv, flag) {
  const i = argv.indexOf(flag);
  return i !== -1 && i + 1 < argv.length ? argv[i + 1] : null;
}

/**
 * 截断 stdout 缓冲中已完整消费的 "QINGCI_*" 信号前缀（RS...RS 包裹）。
 * 保留未闭合的尾部（可能是被 chunk 切半的信号），避免每次 data 反复重扫已消费信号。
 */
function trimConsumed(buf) {
  let idx = 0;
  let lastEnd = 0;
  while (true) {
    idx = buf.indexOf(RS + "QINGCI_", idx);
    if (idx === -1) break;
    const end = buf.indexOf(RS, idx + 2);
    if (end === -1) break; // 信号未完整，保留等待续尾
    lastEnd = end + 1;
    idx = end + 1;
  }
  return lastEnd > 0 ? buf.slice(lastEnd) : buf;
}

/**
 * 解析实例元数据：运行 `python main.py --resolve-instance <args>` 并解析第一行 JSON。
 * 复用与真实启动完全一致的实例推导逻辑，得到 data_dir/port/host。
 */
function resolveInstance() {
  const { cmd, script } = resolvePython();
  const args = [];
  if (script) args.push(script);
  args.push("--resolve-instance", ...forwardArgs());
  let res;
  try {
    res = spawnSync(cmd, args, { encoding: "utf8", timeout: 20000, env: backendEnv() });
  } catch (e) {
    throw new Error(`后端实例解析进程启动失败: ${e.message}`);
  }
  if (res.status !== 0) {
    throw new Error(`实例解析失败(status=${res.status}): ${(res.stderr || "").slice(0, 800)}`);
  }
  const line = (res.stdout || "").split(/\r?\n/).find((l) => l.trim().startsWith("{"));
  if (!line) throw new Error("实例解析未返回 JSON 元数据");
  return JSON.parse(line);
}

// ── per-data-dir 单实例（控制端口） ─────────────────────────
// data-dir -> 稳定控制端口，作为同目录实例的唯一标识：
//  - 本进程能成功监听该端口 => 首个实例，继续
//  - 端口被占用 => 已有同目录实例，向其发出 "focus" 后退出
function controlPortFor(dataDir) {
  const hash = crypto.createHash("sha256").update(dataDir.toLowerCase()).digest();
  return 55000 + (hash.readUInt16BE(0) % 2000); // 55000~56999
}

function acquireSingleInstance() {
  const meta = resolveInstance();
  const port = controlPortFor(meta.data_dir);
  return new Promise((resolve, reject) => {
    const server = net.createServer((sock) => {
      sock.on("data", (d) => {
        if (d.toString().trim() === "focus") {
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.show();
            mainWindow.restore();
            mainWindow.focus();
          }
        }
      });
    });
    server.once("error", (err) => {
      if (err.code === "EADDRINUSE") {
        // 已有同名实例：通知其聚焦并放弃本进程
        const c = net.connect({ port, host: "127.0.0.1" });
        c.on("connect", () => {
          c.write("focus");
          setTimeout(() => c.end(), 100);
        });
        c.on("error", () => {});
        resolve({ isPrimary: false, meta });
      } else {
        reject(err);
      }
    });
    server.listen(port, "127.0.0.1", () => {
      resolve({ isPrimary: true, meta, server });
    });
  });
}

// ── 全局状态 ─────────────────────────────────────────────────
let mainWindow = null;
let tray = null;
let backend = null; // spawned Python 子进程
let controlServer = null;
let isQuitting = false;
let instanceMeta = null;
let pendingRelaunchArgs = null; // 后端请求切实例时的目标启动参数（QINGCI_RELAUNCH 信号）
let readyTimer = null;          // 后端就绪超时定时器
let abnormalExitCount = 0;      // 连续异常退出计数（防抖）
let lastAbnormalExit = 0;       // 上一次异常退出时间戳
let _stableDataRoot = null;     // 打包形态下后端可写数据根（稳定用户数据目录）

// RELAUNCH 信号参数白名单：仅允许派生自后端 _build_start_args 的转发标志
const RELAUNCH_ALLOWED = new Set(["--instance", "--data-dir", "--host", "--port", "--config", "--rename-dir", "--no-bot"]);

function clearReadyTimer() {
  if (readyTimer) { clearTimeout(readyTimer); readyTimer = null; }
}

// 校验/清洗 RELAUNCH 目标参数：含非白名单标志时整体拒绝，避免异常内容触发异常重启
function sanitizeRelaunchArgs(arr) {
  if (!Array.isArray(arr)) return null;
  const out = [];
  for (const t of arr) {
    if (typeof t !== "string") return null;
    if (t.startsWith("--") && !RELAUNCH_ALLOWED.has(t)) return null;
    out.push(t);
  }
  return out;
}

/**
 * 后端进程环境变量。打包（便携/安装）形态下注入 QINGCI_USER_DATA，让后端把
 * 实例/数据落到稳定用户目录——便携版 EXE 解压到临时目录运行，数据若留在其中
 * 会在退出或系统清理临时目录时丢失。开发/onedir 直跑不注入，维持随目录分发。
 */
function backendEnv() {
  const env = Object.assign({}, process.env);
  if (_stableDataRoot) env.QINGCI_USER_DATA = _stableDataRoot;
  return env;
}

function makeWindow({ error = false } = {}) {
  const win = new BrowserWindow({
    width: error ? 640 : 1100,
    height: error ? 420 : 750,
    minWidth: error ? 480 : 800,
    minHeight: error ? 300 : 600,
    title: "Qingci-Bot CE",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "..", "build", "icon.png"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  // 点击关闭 = 隐藏驻留托盘（真正退出走托盘「退出」）
  win.on("close", (e) => {
    if (!isQuitting) {
      e.preventDefault();
      win.hide();
    }
  });
  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
  });
  return win;
}

function showBootWindow() {
  const win = makeWindow();
  mainWindow = win;
  win.loadFile(path.join(__dirname, "..", "build", "boot.html"));
  win.show();
  return win;
}

function loadAppURL(win, port) {
  win.loadURL(`http://${instanceMeta.host}:${port}/ui`);
  win.show();
}

function launchError(msg) {
  elog("[electron] 启动失败:", String(msg));
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
      <body style="background:#0b0f1a;color:#e5e7eb;font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0">
        <h2 style="color:#fbbf24">启动失败</h2>
        <p style="white-space:pre-wrap;max-width:540px;color:#9ca3af">${String(msg)}</p>
        <button id="q" style="margin-top:20px;padding:8px 24px;background:#38bdf8;border:none;color:#0b0f1a;border-radius:6px;cursor:pointer">退出</button>
        <script>
          document.getElementById('q').addEventListener('click', () => { window.qingci && window.qingci.quit(); });
        </script>
      </body>`)}`);
    mainWindow.show();
  } else {
    const win = makeWindow({ error: true });
    mainWindow = win;
    win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
      <body style="background:#0b0f1a;color:#e5e7eb;font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0">
        <h2 style="color:#fbbf24">启动失败</h2>
        <p style="white-space:pre-wrap;max-width:540px;color:#9ca3af">${String(msg)}</p>
        <button id="q" style="margin-top:20px;padding:8px 24px;background:#38bdf8;border:none;color:#0b0f1a;border-radius:6px;cursor:pointer">退出</button>
        <script>
          document.getElementById('q').addEventListener('click', () => { window.qingci && window.qingci.quit(); });
        </script>
      </body>`)}`);
    win.show();
  }
}

function showTray() {
  const iconPath = path.join(__dirname, "..", "build", "icon.png");
  const icon = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip("Qingci-Bot CE");
  const menu = Menu.buildFromTemplate([
    { label: "显示窗口", click: () => { if (mainWindow) { mainWindow.show(); mainWindow.restore(); } } },
    { type: "separator" },
    {
      label: "退出",
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);
  tray.setContextMenu(menu);
  tray.on("click", () => { if (mainWindow) { mainWindow.show(); mainWindow.restore(); } });
}

/**
 * spawn Python 后端进程，等待 READY 信号。
 * relaunchArgs：切实例/改名重启时为后端指定目标启动参数（--instance etc.，
 * 由后端 QINGCI_RELAUNCH 信号交回）；否则沿用 Electron 启动时的原始参数。
 */
function startBackend(relaunchArgs = null) {
  const { cmd, script } = resolvePython();
  const args = [];
  // 开发模式始终带脚本路径（relaunch 亦然）；打包模式 script 为 null，直接走 exe
  if (script) {
    args.push(script);
  }
  args.push("--backend");
  args.push(...(relaunchArgs && relaunchArgs.length ? relaunchArgs : forwardArgs()));

  backend = spawn(cmd, args, {
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
    env: backendEnv(),
  });
  elog("[electron] spawn 后端:", cmd, args.join(" "));

  // B2：就绪超时保护，避免无限卡在启动画面
  clearReadyTimer();
  readyTimer = setTimeout(() => {
    clearReadyTimer();
    elog("[electron] 后端就绪超时（90s），终止该进程");
    if (backend && !backend.killed) { try { backend.kill(); } catch (_e) {} }
    launchError(`后端启动超时（90 秒）未就绪，进程已终止。\n日志：${logHintPath()}`);
  }, 90000);

  let stdoutBuf = "";
  backend.stdout.on("data", (chunk) => {
    stdoutBuf += chunk.toString();
    // QINGCI_READY <port>：后端就绪，供 UI 加载
    const rIdx = stdoutBuf.indexOf(RS + "QINGCI_READY ");
    if (rIdx !== -1) {
      const val = stdoutBuf.substring(rIdx + 2); // 跳过起始 RS
      const end = val.indexOf(RS);
      const tok = (end === -1 ? val : val.substring(0, end)).trim();
      const port = parseInt(tok.split(/\s+/)[1], 10);
      if (port) {
        instanceMeta.port = port;
        clearReadyTimer();
        abnormalExitCount = 0;
        elog("[electron] 后端就绪，port:", port);
        onBackendReady(port);
      }
    }
    // QINGCI_RELAUNCH <json>：后端请求切实例，记录目标参数待退出后重新拉起
    const lIdx = stdoutBuf.indexOf(RS + "QINGCI_RELAUNCH ");
    if (lIdx !== -1) {
      const val = stdoutBuf.substring(lIdx + 2); // 跳过起始 RS
      const end = val.indexOf(RS);
      const tok = (end === -1 ? val : val.substring(0, end)).trim();
      const j = tok.indexOf(" ");
      const jsonStr = j === -1 ? "" : tok.substring(j + 1);
      if (jsonStr) {
        try {
          const parsed = JSON.parse(jsonStr);
          const cleaned = sanitizeRelaunchArgs(parsed);
          if (cleaned && cleaned.length) {
            pendingRelaunchArgs = cleaned;
            abnormalExitCount = 0;
            elog("[electron] 收到 RELAUNCH 请求:", jsonStr);
          } else {
            pendingRelaunchArgs = null;
            elog("[electron] RELAUNCH 参数不在白名单或为空，已忽略:", jsonStr);
          }
        } catch (e) {
          pendingRelaunchArgs = null;
          console.error("[electron] RELAUNCH 参数解析失败:", e);
          elog("[electron] RELAUNCH 参数解析失败:", String(e && e.message || e));
        }
      }
    }
    // 截断已消费的 QINGCI_* 信号，避免每个 chunk 反复重扫
    stdoutBuf = trimConsumed(stdoutBuf);
  });
  // 后端 stderr 一并采集到 electron 日志（后端自身文件日志之外的最小诊断兜底）
  backend.stderr.on("data", (chunk) => {
    const s = chunk.toString().trim();
    if (s) { elog("[backend] ", s); }
  });
  backend.on("error", (err) => {
    clearReadyTimer();
    elog("[electron] 后端进程启动失败:", String(err && err.message || err));
    console.error("[electron] 后端进程启动失败:", err.message);
    launchError(`后端进程启动失败：${err.message}\n日志：${logHintPath()}`);
  });
  backend.on("exit", (code) => {
    clearReadyTimer();
    if (isQuitting) return;
    if (pendingRelaunchArgs) {
      const target = pendingRelaunchArgs;
      pendingRelaunchArgs = null;
      elog("[electron] 按 RELAUNCH 切实例");
      handleRelaunch(target);
      return;
    }
    if (code !== 0) {
      // B3：连续异常退出计数，达阈值即停止（提示排查，避免反复拉起-崩溃）
      const now = Date.now();
      if (now - lastAbnormalExit > 5000) abnormalExitCount = 0;
      lastAbnormalExit = now;
      abnormalExitCount++;
      elog(`[electron] 后端异常退出 (${code})，连续第 ${abnormalExitCount} 次`);
      if (abnormalExitCount >= 3) {
        launchError(`后端进程连续退出（${abnormalExitCount} 次），已停止自动重启。\n请检查日志：${logHintPath()}`);
        return;
      }
      launchError(`后端进程异常退出 (${code})\n日志：${logHintPath()}`);
    }
  });
}

/**
 * 切实例/改名：后端退出后由 Electron 直接拉起新进程，壳不退出。
 * 支持 --rename-dir from to：在旧进程退出（文件锁释放）后改名，再启动新实例。
 * 实例根目录从当前 data_dir 推导（<根>/<name>/data），开发与打包（resources/backend）
 * 两种布局统一正确。
 */
function handleRelaunch(args) {
  abnormalExitCount = 0;
  clearReadyTimer();
  // 实例根目录 = data_dir 上溯两级；打包时 data_dir 位于 resources/backend/instances/<n>/data
  const base = (instanceMeta && instanceMeta.data_dir)
    ? path.resolve(instanceMeta.data_dir, "..", "..")
    : path.join(APP_ROOT, "instances");

  const renameIdx = args.indexOf("--rename-dir");
  if (renameIdx !== -1 && renameIdx + 2 < args.length) {
    const from = args[renameIdx + 1];
    const to = args[renameIdx + 2];
    args.splice(renameIdx, 3);
    const fromDir = path.join(base, from);
    const toDir = path.join(base, to);
    try {
      if (fs.existsSync(fromDir) && !fs.existsSync(toDir)) {
        fs.renameSync(fromDir, toDir);
      }
    } catch (e) {
      console.error("[electron] 实例改名失败（不阻塞启动）:", e);
    }
  }

  // 刷新 instanceMeta 指向目标实例，使随后的 loadAppURL / 后续切换读到新元数据；
  // 无显式 --data-dir（常见：仅透传 --instance）时按实例根目录推导目标 data。
  const inst = argValue(args, "--instance") || (instanceMeta && instanceMeta.instance) || null;
  if (instanceMeta && inst) {
    const dd = argValue(args, "--data-dir");
    instanceMeta.instance = inst;
    instanceMeta.data_dir = dd ? path.resolve(dd) : path.join(base, inst, "data");
  }

  // 新后端就绪后刷新窗口到新实例端口
  const prevHandler = onBackendReadyHandler;
  onBackendReadyHandler = (port) => {
    instanceMeta.port = port;
    const win = mainWindow && !mainWindow.isDestroyed() ? mainWindow : showBootWindow();
    loadAppURL(win, port);
    onBackendReadyHandler = prevHandler;
  };
  startBackend(args);
}

let onBackendReadyHandler = null;
function onBackendReady(port) {
  if (onBackendReadyHandler) onBackendReadyHandler(port);
}

// 启动失败页的「退出」按钮
ipcMain.on("quit", () => {
  isQuitting = true;
  app.quit();
});

app.whenReady().then(async () => {
  // 打包（便携/安装）形态下，将后端可写数据根指向稳定的用户数据目录
  // （%APPDATA%/Qingci-Bot-CE），避免便携版解压运行的实例数据落到系统临时目录。
  if (!isDev()) {
    app.setPath("userData", path.join(app.getPath("appData"), "Qingci-Bot-CE"));
    _stableDataRoot = app.getPath("userData");
  }
  try {
    const { isPrimary, meta, server } = await acquireSingleInstance();
    instanceMeta = meta;
    _logInit(instanceMeta.data_dir);
    elog("[electron] 壳启动，instance:", instanceMeta.instance || "", "data-dir:", instanceMeta.data_dir, "host:", instanceMeta.host);
    if (!isPrimary) {
      // 同目录已有实例，已通知聚焦，本进程退出
      app.quit();
      return;
    }
    controlServer = server;

    showBootWindow();
    showTray();
    startBackend();

    // 后端就绪后加载真实 UI（复用启动窗口）
    onBackendReadyHandler = (port) => {
      const win = mainWindow && !mainWindow.isDestroyed() ? mainWindow : showBootWindow();
      loadAppURL(win, port);
    };
  } catch (err) {
    elog("[electron] 启动流程异常:", String(err && err.message || err));
    launchError(String(err && err.message ? err.message : err));
  }
});

// 后台完全退出时回收后端进程
app.on("before-quit", () => {
  isQuitting = true;
});
app.on("will-quit", () => {
  if (backend && !backend.killed) {
    try {
      backend.kill();
    } catch (e) { /* ignore */ }
  }
  if (controlServer) {
    try { controlServer.close(); } catch (e) { /* ignore */ }
  }
  if (tray) tray.destroy();
});
app.on("window-all-closed", () => {
  // 关闭即驻留托盘，不退出（除非 isQuitting）。不调用 app.quit() 即可驻留。
  if (isQuitting) app.quit();
});
app.on("activate", () => {
  if (mainWindow) { mainWindow.show(); mainWindow.restore(); }
});