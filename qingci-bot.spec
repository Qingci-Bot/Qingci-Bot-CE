# -*- mode: python ; coding: utf-8 -*-
"""Qingci-Bot PyInstaller 打包配置（onedir 模式）

产物结构：
    dist/qingci-bot/
        qingci-bot.exe        # 主程序（windowed，无控制台窗口）
        _internal/            # Python 运行时与依赖
        web/dist/             # Web UI（构建脚本复制，不打包进 exe）
        config.yaml           # 用户配置（构建脚本复制，不打包进 exe）
        data/                 # SQLite 数据、备份、词库（运行时生成）

可写资源与静态资源均按"exe 所在目录"相对路径读取（见 bot/paths.py），
因此不通过 datas 打进包内，由 build.ps1 复制到产物目录分发。

当前为 windowed（无控制台）模式（console=False），日志不可见，
建议配合 config.yaml 的文件日志使用；如需控制台可将 EXE 参数改回 console=True。
"""

from PyInstaller.utils.hooks import collect_all

# litellm 携带大量数据文件（模型/provider 映射 JSON），整体收集
litellm_datas, litellm_binaries, litellm_hiddenimports = collect_all('litellm')

# tiktoken 的编码数据经 tiktoken_ext 插件包加载，缺失会导致
# "Unknown encoding cl100k_base"，需整体收集并显式导入
tiktoken_datas, tiktoken_binaries, tiktoken_hiddenimports = collect_all('tiktoken_ext')

# desktop 模式：pywebview 的 EdgeChromium 后端依赖 pythonnet（.NET 运行时）；
# pythonnet/runtime 下的 DLL 是数据文件，不会被 modulegraph 自动收集，
# 需 collect_all 一并打入
pythonnet_datas, pythonnet_binaries, pythonnet_hiddenimports = collect_all('pythonnet')
clrloader_datas, clrloader_binaries, clrloader_hiddenimports = collect_all('clr_loader')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=litellm_binaries + tiktoken_binaries + pythonnet_binaries + clrloader_binaries,
    datas=litellm_datas + tiktoken_datas + pythonnet_datas + clrloader_datas,
    hiddenimports=[
        *litellm_hiddenimports,
        *tiktoken_hiddenimports,
        *pythonnet_hiddenimports,
        *clrloader_hiddenimports,
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
        # ---- 新功能模块（延迟导入，modulegraph 不会自动收集）----
        'bot.core.message',          # 类型化消息构造器（Message/MessageSegment）
        'bot.llm.mcp',               # MCP 桥接（setup_mcp_tools 内延迟导入）
        'mcp',
        'mcp.client.stdio',
        'mcp.client.streamable_http',
        # ---- desktop 模式（pywebview / pystray） ----
        'webview',
        # pywebview 平台后端为动态导入，需显式声明（Windows: WinForms + EdgeChromium）
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        # pythonnet/.NET 加载链
        'clr',
        'clr_loader',
        'pythonnet',
        'pystray',
        'pystray._win32',
        'PIL',
        # desktop/main.py 中以模块名导入入口脚本
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
    name='qingci-bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,       # console=True 便于查看日志；改 False 可去掉控制台窗口
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='qingci-bot',
)
