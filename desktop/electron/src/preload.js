"use strict";

/**
 * Electron preload：在渲染进程暴露最小、安全的 IPC 桥梁。
 * 上下文隔离开启，仅用于「启动失败页面」的退出按钮等极少数场景。
 */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("qingci", {
  quit: () => ipcRenderer.send("quit"),
});