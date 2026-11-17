# 打包与发布

> PyInstaller 后端 onedir、Electron 桌面壳（Windows EXE / Linux deb+rpm）与 GitHub Actions 发布流水线。
> 构建前置的开发环境见 [CONTRIBUTING.md](../CONTRIBUTING.md)。


使用 PyInstaller 将 Qingci-Bot CE 打包为 Windows 可执行程序（onedir 模式）。

## 构建

```powershell
# 依赖：PyInstaller 已安装在 .venv（uv pip install pyinstaller）
# Web UI 需先构建（web\dist 存在时可跳过）
cd web; npm install; npm run build; cd ..

# 一键打包
.\build.bat

# 轻量包（跳过 Chromium 捆绑，产物轻约百 MB；渲染能力按需事后安装）
.\build.bat --no-browser
```

> `instances/` 目录已被 `.gitignore` 忽略（其中的 `config.yaml` 可能含密钥）。新克隆的仓库中没有该目录，首次启动会自动创建 `default` 实例并生成其 `config.yaml`；如需预先配置，可参考 `config.example.yaml` 在 `instances/default/config.yaml` 中填写。

产物位于 `dist\qingci-bot-ce\`：

```
dist\qingci-bot-ce\
├── qingci-bot-ce.exe       # 主程序（windowed 无控制台窗口；日志请开启文件日志）
├── _internal\              # Python 运行时与依赖（勿动）
├── ms-playwright\          # 内置 Playwright 无头浏览器（build.bat 下载，HTML 渲染用）
├── web\dist\               # Web UI 静态资源（build.bat 复制）
└── instances\              # 实例目录（首次启动自动创建 default 实例，含 config.yaml/plugins/data）
```

> 自 v1.5.1 起配置/插件/数据已收敛到 `instances\<name>\` 自包含目录，构建产物不再生成根级 `config.yaml` 或 `data\`。用户数据（配置、插件、数据库、日志）均按实例隔离，随实例目录一起分发。

> **HTML 渲染全内置**：`build.bat` 会下载 Playwright 无头 Chromium 到产物目录 `ms-playwright\`，运行时 `main.py` 通过 `PLAYWRIGHT_BROWSERS_PATH` 指向它，EXE 开箱即可渲染签到卡等 HTML → 图片，无需最终用户另行 `playwright install chromium`。若构建环境网络不佳导致下载失败，产物仍可运行，仅渲染能力降级（签到卡回退纯文本）。构建期可设 `set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright` 走国内镜像加速。

> **按需化（轻量包）**：不需要开箱渲染、想减小分发体积时，可用 `--no-browser`（Windows）/ `--no-browser`（Linux，或设 `SKIP_BROWSER=1`）跳过浏览器捆绑；产物不含 `ms-playwright\`，需要渲染能力时在产物目录执行 `playwright install chromium`（`PLAYWRIGHT_BROWSERS_PATH` 指向产物目录）。GitHub Actions 手动触发 Release 时可在 `bundle_browser` 输入中选择是否捆绑。默认（tag 推送）仍捆绑，保持开箱可渲染。

## 运行

```powershell
.\dist\qingci-bot-ce\qingci-bot-ce.exe              # Bot + API 服务
.\dist\qingci-bot-ce\qingci-bot-ce.exe --no-bot     # 仅 API / Web UI
.\dist\qingci-bot-ce\qingci-bot-ce.exe --port 9000  # 指定端口
```

启动后访问 `http://127.0.0.1:8080/ui/`。

> **启动性能**：litellm 采用延迟导入，启动阶段不会加载该重型依赖（节省约 3.5 秒），仅首次真正调用 LLM 时一次性导入；首次运行自动创建 `default` 实例并生成其 `config.yaml`，无需手工准备。

## 注意事项

- 直接命令行运行 onedir 时，实例目录 `instances\` 按 **exe 所在目录** 相对定位（`app_root`）：分发时整个 `dist\qingci-bot-ce\` 目录一起拷贝，勿单独移动 exe。若设置了环境变量 `QINGCI_DATA_HOME`，则以该目录为准（Electron 壳拉起后端时正是这样把数据指向安装时选定的目录）。

- 首次运行自动创建 `instances\default\` 实例并生成默认 `config.yaml`；数据库自动建表（SQLModel create\_all）。

- 重新执行 `build.bat` 不会覆盖 `instances\` 中已有的实例配置与数据（用户数据始终保留在实例目录内）。

- 当前产物为 **windowed 无控制台** 模式（`qingci-bot-ce.spec` 中 `console=False`），日志不直接可见，建议开启文件日志（`log.log_file_enabled: true`）；如需控制台窗口排障，将 `console` 改为 `True` 后重新构建。

- 桌面 GUI 由 Electron 壳（`desktop\electron\`）承担：分发用 `build-electron.bat` 生成桌面壳 EXE（安装版 + 绿色解压 zip），其内嵌 Python 后端（`--backend`）就绪时通过标准输出上报端口供壳加载 Web UI；不依赖系统 WebView2 / pywebview。

## 桌面壳（Electron）打包

`build.bat` 产出的 onedir 是"后端形态"（可加 `--backend` 由壳拉起，也可直接命令行运行 Bot/API）。要给最终用户一个免配置的桌面应用，再跑第二步 `build-electron.bat` 出安装版 / 绿色解压版 EXE。

```powershell
# 先决条件
#  1) 已执行 .\build.bat，产物位于 dist\qingci-bot-ce\（后端 onedir）
#  2) Node.js 22+ 与 npm 可用（Electron 45.2.x，与 CI 的 Node 22 一致）
.\build-electron.bat
```

- 产物（`dist\electron\`）：`Qingci-Bot-CE-<version>-Setup-x64.exe`（NSIS 安装向导，可选安装目录与数据目录）与 `Qingci-Bot.CE-<version>-win.zip`（绿色解压版，无向导；electron-builder 把产品名的空格转成了点）；后端以单个 `backend.zip` 放在 `resources\`，Playwright Chromium 随其一并内置。

```powershell
.\dist\electron\Qingci-Bot-CE-<version>-Setup-x64.exe   # 安装版
# 绿色解压版：解压 zip 后运行其中的 Qingci-Bot CE.exe
```

- **运行方式**：双击桌面 EXE（安装版或绿色解压版）即由 Electron 壳启动——先解析当前实例 → spawn 内嵌 Python 后端（`--backend`）→ 就绪后加载 Web UI；系统托盘、按 data-dir 的单实例锁与聚焦、启动画面、关闭驻留后台全部由壳承担。

- **数据位置**：**数据不在安装目录内**。安装向导有一步「选择数据目录」（默认 `%LOCALAPPDATA%\Qingci-Bot CE\data`），选择结果记在注册表 `HKCU\Software\Qingci-Bot CE` 与 `%APPDATA%\Qingci-Bot CE\datadir.txt`；`instances\<name>\` 与后端解压产物 `backend\<版本>\` 都落在该目录下。绿色解压版没有向导，直接使用默认目录（存在 `datadir.txt` 时以它为准）。升级与卸载只清理安装目录，不会触碰数据；旧版本放在 EXE 旁 `instances/` 的数据首次启动会自动迁移。

- **调试**：壳主进程日志写到该实例的数据目录（`<数据目录>\instances\<name>\data\logs\electron.log`）；后端超时（默认 90s 未就绪）会展示错误页并可重试/查看日志。

## Linux 打包（deb / rpm）

Linux 分发**不使用 AppImage**，改为原生包管理的 **deb**（Debian / Ubuntu）与 **rpm**（Fedora / RHEL / openSUSE）。每个发行版格式出两款包：

| 包 | 内容 | 安装位置 | 适用场景 |
| --- | --- | --- | --- |
| `Qingci-Bot-CE` | Electron 桌面壳 + 内嵌后端 | `/opt/Qingci-Bot CE` | 有图形环境的桌面使用 |
| `Qingci-Bot-CE-server` | headless 后端 + systemd 单元 | `/opt/qingci-bot-ce-server` | 无桌面服务器 / 长期驻留 |

两款包的共同前提是先产出 Python 后端 onedir（`build-linux.sh`，对应 Windows 的 `build.bat`，输出 `dist/qingci-bot-ce/`）。

### 1. 桌面版（electron-builder）

```bash
# 先决条件：.venv + [build] 依赖、Node.js 22+、系统需 rpm 与 fakeroot（生成 rpm 依赖）
./build-linux.sh
cd desktop/electron && npm ci && npx electron-builder --linux deb rpm --publish never
```

- 产物：`dist/electron/Qingci-Bot-CE-<version>-x64.deb` 与 `.rpm`（内嵌后端为 `resources/backend.zip`，首次启动解压到数据目录）。
- 安装：`sudo apt install ./Qingci-Bot-CE-<ver>-x64.deb` 或 `sudo dnf install ./Qingci-Bot-CE-<ver>-x64.rpm`。
- 数据位置：按 XDG 规范落在 `$XDG_DATA_HOME/Qingci-Bot CE`（默认 `~/.local/share/Qingci-Bot CE`），**不在 `/opt` 内**——包升级会整体替换 `/opt` 下的程序文件。
- 依赖声明：deb/rpm 的 `depends` 见 `desktop/electron/package.json` 的 `build.deb` / `build.rpm`（GTK3 / NSS / libnotify 等 Electron 运行时库）。

> **为什么不用 AppImage**：AppImage 以 squashfs 只读挂载运行，程序目录无法写入；且其运行方式下后端二进制解压后需依赖 zip 权限位还原，链路更脆弱。deb/rpm 走标准包管理，权限、依赖、卸载行为都由发行版工具保证。

### 2. 服务端版（headless + systemd）

```bash
bash scripts/pack_linux_server.sh --version 1.20.0          # 同时出 deb 与 rpm
bash scripts/pack_linux_server.sh --version 1.20.0 --only rpm
```

- 产物：`dist/linux/Qingci-Bot-CE-server_<ver>_<arch>.deb`（文件名按 dpkg 规范）与 `Qingci-Bot-CE-server-<ver>-1.<arch>.rpm`。
- 布局（遵循 FHS）：程序 `/opt/qingci-bot-ce-server`、实例数据 `/var/lib/qingci-bot-ce-server/instances/<name>`、日志 `/var/log/qingci-bot-ce-server/`。
- 安装即创建系统用户 `qingci-bot`，注册并 `enable` systemd 单元 `qingci-bot-ce.service`；**首次安装不自动 start**，确认 `config.yaml` 后手动 `systemctl start`。
- 卸载保留 `/var/lib` 下的实例数据；`apt purge` 才删除。
- 不依赖 Electron / X11，纯 Python 冻结产物 + systemd，可直接跑在无桌面服务器上。

> 构建机需具备 `dpkg-deb`（deb）与 `rpmbuild`（rpm）：缺哪个就跳过哪种格式，用 `--only` 可强制只出一种并在缺失时报错。

## 自动发布（GitHub Actions）

打 `vX.Y.Z` tag（或在 Actions 里手动触发 `workflow_dispatch`）即自动构建并上传分发包到同名 GitHub Release，同时构建并推送 Docker 镜像到 `ghcr.io/<repo>`——无需手动上传。流水线见 [`.github/workflows/release.yml`](../.github/workflows/release.yml)，各平台自动联编：
- Windows 桌面 EXE（安装版 Setup.exe + 绿色解压 zip，`build.bat` + `build-electron.bat`）
- Linux deb / rpm 共 4 个产物（`build-linux.sh` + `electron-builder --linux deb rpm` 出桌面版，`scripts/pack_linux_server.sh` 出服务端版）
- Docker 镜像（tag：`vX.Y.Z` / `X.Y` / `X` / `latest`）
