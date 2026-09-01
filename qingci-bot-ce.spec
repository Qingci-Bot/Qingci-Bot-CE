# -*- mode: python ; coding: utf-8 -*-
"""Qingci-Bot CE PyInstaller 打包配置（onedir 模式）

产物结构：
    dist/qingci-bot-ce/
        qingci-bot-ce.exe        # 主程序（windowed，无控制台窗口）
        _internal/               # Python 运行时与依赖
        ms-playwright/           # 内置 Playwright 无头浏览器（build.ps1 下载）
        web/dist/                # Web UI（构建脚本复制，不打包进 exe）
        instances/               # 实例目录（首次启动自动创建，含 config.yaml/plugins/data）

可写资源与静态资源均按"exe 所在目录"相对路径读取（见 bot/paths.py），
因此不通过 datas 打进包内，由 build.ps1 复制到产物目录分发。
自 v1.6 起配置/插件/数据已收敛到 instances/<name>/ 自包含目录，
构建产物不再生成根级 config.yaml 或 data\。

当前为 windowed（无控制台）模式（console=False），日志不可见，
建议配合 config.yaml 的文件日志使用；如需控制台可将 EXE 参数改回 console=True。
"""

import os
import shutil

from PyInstaller.utils.hooks import collect_all

# 过滤目标：litellm/proxy/_experimental/out 的前端静态产物子树
_LITELLM_OUT_PREFIX = ("litellm", "proxy", "_experimental", "out")

# litellm 携带大量数据文件（模型/provider 映射 JSON），整体收集。
# 注意：litellm.proxy 的模块被 __init__.py 顶层导入（proxy_cli），不可排除整个
# proxy；但其 proxy/_experimental/out 是 Next.js Web 前端静态产物（约 22MB），
# exe 内从不使用，故从 datas 中过滤以减小体积。
litellm_datas_all, litellm_binaries, litellm_hiddenimports = collect_all('litellm')
litellm_datas = [
    (src, dst)
    for src, dst in litellm_datas_all
    if not str(dst).split(os.sep)[:4] == list(_LITELLM_OUT_PREFIX)
]

# tiktoken 的编码数据经 tiktoken_ext 插件包加载，缺失会导致
# "Unknown encoding cl100k_base"，需整体收集并显式导入
tiktoken_datas, tiktoken_binaries, tiktoken_hiddenimports = collect_all('tiktoken_ext')

# 独立插件 SDK（qingci_plugin_sdk）：外部插件运行时 import 它，必须随主程序
# 打包；整体收集全部子模块，保证数据目录重定向等特性可用
sdk_datas, sdk_binaries, sdk_hiddenimports = collect_all('qingci_plugin_sdk')

# HTML 渲染（可选能力）：playwright Python 包随 EXE 收集，保证渲染代码可导入；
# 浏览器二进制不进 EXE（体积大），由 build.ps1 下载到产物目录 ms-playwright/，
# 运行时经 PLAYWRIGHT_BROWSERS_PATH（见 main.py）定位
pw_datas, pw_binaries, pw_hiddenimports = collect_all('playwright')

# 内嵌 pip：打包模式下自动安装外部插件依赖（bot/plugin/deps.py 走
# pip._internal）到实例 deps 目录，需随产物整体收集 pip 及其 vendored 依赖
pip_datas, pip_binaries, pip_hiddenimports = collect_all('pip')

# 内嵌 uv：打包模式下优先用 uv 子进程安装外部插件依赖（比 pip 更快、更干净）。
# uv 是独立二进制而非 Python 模块，用 datas 打进 _MEIPASS，运行时由
# bot/plugin/deps.py 经 sys._MEIPASS 定位并作为子进程调用；缺失则回退内嵌 pip。
_uv_exe = shutil.which("uv")
uv_datas = [(_uv_exe, ".")] if _uv_exe else []
if _uv_exe:
    print(f"[spec] bundling uv -> {_uv_exe}")
else:
    print("[spec] WARNING: uv not found on PATH; plugin deps will fall back to bundled pip")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=litellm_binaries + tiktoken_binaries + sdk_binaries + pip_binaries + pw_binaries,
    datas=litellm_datas + tiktoken_datas + sdk_datas + pip_datas + pw_datas + uv_datas + [
        ('desktop\\assets\\app-icon.ico', '.'),
    ],
    hiddenimports=[
        *litellm_hiddenimports,
        *tiktoken_hiddenimports,
        *sdk_hiddenimports,
        *pip_hiddenimports,
        *pw_hiddenimports,
        'tiktoken_ext.openai_public',
        # ---- uvicorn 运行时动态导入（五件套 + standard 附加依赖） ----
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'httptools',
        'watchfiles',
        'dotenv',
        'websockets',
        'websockets.legacy',
        # ---- 数据库 ----
        'aiosqlite',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.dialects.sqlite.aiosqlite',
        # ---- Bot 依赖 ----
        'aiocqhttp',
        'apscheduler',
        'pydantic',
        'httpx',
        # ---- 内置插件（pkgutil.iter_modules 动态发现，modulegraph 不会自动收集）----
        'bot.plugin.builtin.chat',
        'bot.plugin.builtin.admin',
        'bot.plugin.builtin.help',
        'bot.plugin.builtin.imagegen',
        'bot.plugin.builtin.knowledge',
        # ---- 新功能模块（延迟导入，modulegraph 不会自动收集）----
        'bot.core.message',          # 类型化消息构造器（Message/MessageSegment）
        'bot.llm.mcp',               # MCP 桥接（setup_mcp_tools 内延迟导入）
        'mcp',
        'mcp.client.stdio',
        'mcp.client.streamable_http',
        # ---- 向量知识库（VectorKnowledgeStore 内延迟导入）----
        'lancedb',
        'pyarrow',
        # ---- desktop 导出（Python 侧桌面辅助：跨进程重启助手 / 单实例互斥） ----
        'desktop.py.relaunch',
        'desktop.py.single_instance',
        # ---- 以模块名形式被导入的入口脚本 ----
        'main',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大依赖，减小体积
        'tkinter',
        'matplotlib',
        'notebook',
        'pytest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='qingci-bot-ce',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,       # console=True 便于查看日志；改 False 可去掉控制台窗口
    icon='desktop\\assets\\app-icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='qingci-bot-ce',
)