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

> **按需化（轻量包）**：不需要开箱渲染、想减小分发体积时，可用 `--no-browser`（Windows）/ `--no-browser`（Linux，或设 `SKIP_BROWSER=1`）跳过浏览器捆绑；产物不含 `ms-playwright\`。GitHub Actions 手动触发 Release 时可在 `bundle_browser` 输入中选择是否捆绑；默认（tag 推送）仍捆绑，保持开箱可渲染。
>
> **注意**：`--no-browser` 的产物要恢复渲染，目前只适用于**源码部署与 onedir 产物**——在产物目录执行 `playwright install chromium`（`PLAYWRIGHT_BROWSERS_PATH` 指向产物目录）。deb / rpm 安装后 `/opt` 只读、包内也没有 `playwright` CLI，**无法补装浏览器**；这两类安装包需要渲染能力时请使用捆绑浏览器的版本。

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
#  2) Node.js 22+ 与 npm 可用（Electron 45.4.x，与 CI 的 Node 22 一致）
.\build-electron.bat
```

- 产物（`dist\electron\`）：`qingci-bot-ce-<version>-x64-setup.exe`（NSIS 安装向导，可选安装目录与数据目录）与 `qingci-bot-ce-<version>-x64-portable.zip`（绿色解压版，无向导）；后端以单个 `backend.zip` 放在 `resources\`，Playwright Chromium 随其一并内置。命名由 `desktop/electron/package.json` 的 `nsis.artifactName` / `win.artifactName` 决定。

```powershell
.\dist\electron\qingci-bot-ce-<version>-x64-setup.exe      # 安装版
# 绿色解压版：解压 zip 后运行其中的 Qingci-Bot CE.exe
```

- **运行方式**：双击桌面 EXE（安装版或绿色解压版）即由 Electron 壳启动——先解析当前实例 → spawn 内嵌 Python 后端（`--backend`）→ 就绪后加载 Web UI；系统托盘、按 data-dir 的单实例锁与聚焦、启动画面、关闭驻留后台全部由壳承担。

- **数据位置**：**数据不在安装目录内**。安装向导有一步「选择数据目录」（默认 `%LOCALAPPDATA%\Qingci-Bot CE\data`），选择结果记在注册表 `HKCU\Software\Qingci-Bot CE` 与 `%APPDATA%\Qingci-Bot CE\datadir.txt`；`instances\<name>\` 与后端解压产物 `backend\<版本>\` 都落在该目录下。绿色解压版没有向导，直接使用默认目录（存在 `datadir.txt` 时以它为准）。升级与卸载只清理安装目录，不会触碰数据；旧版本放在 EXE 旁 `instances/` 的数据首次启动会自动迁移。

- **调试**：壳主进程日志写到该实例的数据目录（`<数据目录>\instances\<name>\data\logs\electron.log`）；后端超时（默认 90s 未就绪）会展示错误页并可重试/查看日志。

## Linux 打包（deb / rpm）

Linux 分发**不使用 AppImage**，改为原生包管理的 **deb**（Debian / Ubuntu）与 **rpm**（Fedora / RHEL / openSUSE）。每个发行版格式出两款包：

| 包名（deb / rpm 一致） | 内容 | 安装位置 | 适用场景 |
| --- | --- | --- | --- |
| `qingci-bot-ce` | Electron 桌面壳 + 内嵌后端 | `/opt/Qingci-Bot CE`（可执行文件 `qingci-bot-ce`） | 有图形环境的桌面使用 |
| `qingci-bot-ce-server` | headless 后端 + systemd 单元 | `/opt/qingci-bot-ce-server` | 无桌面服务器 / 长期驻留 |

两者各出 **x86_64（amd64）** 与 **arm64（aarch64）** 两个架构（1.22.4 起），共 8 个 Linux 产物。

两款包的共同前提是先产出 Python 后端 onedir（`build-unix.sh`，对应 Windows 的 `build.bat`，输出 `dist/qingci-bot-ce/`）。

### 1. 桌面版（electron-builder）

```bash
# 先决条件：.venv + [build] 依赖、Node.js 22+、系统需 rpm 与 fakeroot（生成 rpm 依赖）
./build-unix.sh
cd desktop/electron && npm ci && npx electron-builder --linux deb rpm --publish never
```

- 产物（`dist/electron/`）：`qingci-bot-ce_<version>_amd64.deb` 与 `qingci-bot-ce-<version>-1.x86_64.rpm`（内嵌后端为 `resources/backend.zip`，首次启动解压到数据目录）。
- 安装：`sudo apt install ./qingci-bot-ce_<ver>_amd64.deb` 或 `sudo dnf install ./qingci-bot-ce-<ver>-1.x86_64.rpm`。
- 数据位置：按 XDG 规范落在 `$XDG_DATA_HOME/Qingci-Bot CE`（默认 `~/.local/share/Qingci-Bot CE`），**不在 `/opt` 内**——包升级会整体替换 `/opt` 下的程序文件。
- 依赖声明：deb/rpm 的 `depends` 见 `desktop/electron/package.json` 的 `build.deb` / `build.rpm`（GTK3 / NSS / libnotify 等 Electron 运行时库）。

> **为什么不用 AppImage**：AppImage 以 squashfs 只读挂载运行，程序目录无法写入；且其运行方式下后端二进制解压后需依赖 zip 权限位还原，链路更脆弱。deb/rpm 走标准包管理，权限、依赖、卸载行为都由发行版工具保证。

### 2. 服务端版（headless + systemd）

```bash
bash scripts/pack_linux_server.sh --version 1.22.0          # 同时出 deb 与 rpm
bash scripts/pack_linux_server.sh --version 1.22.0 --only rpm
```

- 产物：`dist/linux/qingci-bot-ce-server_<ver>_amd64.deb`（文件名按 dpkg 规范）与 `qingci-bot-ce-server-<ver>-1.x86_64.rpm`。
- rpm 的 payload 压缩显式固定为 **zstd-19**（`_binary_payload w19.zstdio`，可用 `RPM_PAYLOAD` 环境变量覆盖）：rpmbuild 的默认值随构建机漂移（Ubuntu 24.04 默认 zstd、22.04 默认 gzip-9），1.22.2 的 server rpm 因此比同内容 deb 大 76 MB；固定后与 deb 基本持平。zstd payload 需 rpm ≥ 4.14，目标发行版均满足。
- 布局（遵循 FHS）：程序 `/opt/qingci-bot-ce-server`、实例数据 `/var/lib/qingci-bot-ce-server/instances/<name>`、日志 `/var/log/qingci-bot-ce-server/`。
- 安装即创建系统用户 `qingci-bot`，注册并 `enable` systemd 单元 `qingci-bot-ce.service`；**首次安装不自动 start**，确认 `config.yaml` 后手动 `systemctl start`。
- 卸载保留 `/var/lib` 下的实例数据；`apt purge` 才删除。
- 不依赖 Electron / X11，纯 Python 冻结产物 + systemd，可直接跑在无桌面服务器上。

> 构建机需具备 `dpkg-deb`（deb）与 `rpmbuild`（rpm）：缺哪个就跳过哪种格式，用 `--only` 可强制只出一种并在缺失时报错。

### 3. arm64（aarch64）

**在原生 arm64 机器上构建，不用 QEMU。** GitHub 托管了 `ubuntu-22.04-arm`，与 x64 的构建基线同代（glibc 2.35），CI 直接在它上面跑完整条链。

| 部件 | 交叉编译 | 做法 |
| --- | --- | --- |
| Python 后端（PyInstaller） | ❌ 不支持 | 在 **arm64 宿主**上原生构建（CI 用 `ubuntu-22.04-arm`） |
| Electron 桌面壳 | ✅ 支持（但没必要） | 同一台 arm64 宿主上 `electron-builder --linux deb rpm`，产物即 arm64 |
| 服务端包（dpkg-deb / rpmbuild） | — | 原生执行 `pack_linux_server.sh`，`uname -m` 给出 aarch64，架构字段自动正确 |

> **历史**：1.22.4 之前的 arm64 包是在 x64 runner 里套一层 `ubuntu:22.04`(arm64) 容器、靠 QEMU 模拟构建的，
> 单个「后端构建」步骤就要 60 分钟以上，比 x64 整条链还慢（实测 67 分钟 vs 21 分钟）。
> 改用原生 runner 后整条链 **12 分钟**，见下节。**不要再退回 QEMU 方案**——`tests/test_workflows.py` 会拦。

```bash
# 本地在 arm64 机器上复现（产物落在 dist/linux，与 x64 产物同名规则）
# 依赖：rpm fakeroot xz-utils file、Python 3.12、Node 22
uv venv .venv && uv pip install --python .venv/bin/python -e ".[build]"
(cd web && npm ci && npm run build)
bash build-unix.sh && .venv/bin/python scripts/pack_backend.py
bash scripts/pack_linux_server.sh --version 1.22.4 --out dist/linux
(cd desktop/electron && npm ci && npx electron-builder --linux deb rpm --publish never)
```

> **为什么原生可行**：PyInstaller 不能交叉编译，必须在 arm64 上构建——原生 runner 正好满足，QEMU 只是模拟。
> 原先用容器的**唯一真实理由**是「基线必须钉在 glibc 2.35」，而 `ubuntu-22.04-arm` 与 `ubuntu-22.04` 同代，
> 产物下限与 x64 完全一致，该理由已不成立（若退到 `ubuntu-24.04-arm`，glibc 2.39 会让 Debian 12 /
> Ubuntu 22.04 装得上却起不来——1.22.0 在 x64 上踩过同一个坑）。
>
> **依赖侧已确认**：锁文件里 17 个编译扩展（numpy / pyarrow / lancedb / tiktoken / tokenizers /
> pydantic-core / uvloop / cryptography / regex 等）全部提供 aarch64 manylinux wheel，arm 上无需源码编译。

## macOS 打包（仅 Apple Silicon）

**只发布 arm64（Apple Silicon）**，且要求 **macOS 13（Ventura）及以上**——不提供 Intel 版本。产物为 `.dmg` 安装镜像与 `.zip` 免安装包。

### 为什么必须在 arm64 宿主上构建

PyInstaller 不能交叉编译，后端只能在目标架构上产出。CI 固定用 `macos-15`（Apple Silicon），并在第一步用 `uname -m` 断言——不用 `macos-latest` 是因为它的架构会随 GitHub 镜像策略漂移（曾从 macos-13 的 x64 切到 macos-14 的 arm64），一旦落到 x64 runner 就会产出「名字写着 arm64、装上跑不了」的包。

### 签名：ad-hoc，以及它为什么不是可选项

Apple Silicon 对 arm64 原生代码**强制要求有效签名**，这与 Gatekeeper 无关。完全未签名的应用双击只会显示「已损坏，无法打开」——不是「无法验证开发者」，是直接打不开。

本项目没有 Apple Developer 证书（每年 99 美元），因此走 **ad-hoc 签名**：

- `package.json` 的 `build.mac.identity` 设为 `"-"`，由 electron-builder 在**制作 dmg 之前**完成签名（顺序很重要：先出 dmg 再补签，dmg 里装的仍是未签名应用）
- ad-hoc 签名没有 Team ID，而 Electron 自带的 Framework 带 Apple Team ID，硬化的运行时（hardened runtime）默认会因库校验拒绝加载 → 必须带 `com.apple.security.cs.disable-library-validation`。这是官方文档给出的首选做法，比关掉 hardened runtime 更保守。entitlements 见 `desktop/electron/build/entitlements.mac.plist`
- PyInstaller 在 macOS 上会自动对收集的二进制做 ad-hoc 签名（arm64 的硬性要求），所以 `backend.zip` 解压出来的后端本身也是已签名的，能被正常 spawn

**用户侧影响**：首次打开需要**右键 →「打开」**（或在「系统设置 → 隐私与安全性」里允许一次）。这是无证书分发的固有代价，不是包损坏。若仍提示已损坏，可执行：

```bash
xattr -dr com.apple.quarantine "/Applications/Qingci-Bot CE.app"
```

应用在首次启动解压后端时会自己清理解压目录的 quarantine 属性（`main.js` 的 `ensureBackendExecutable`），避免后端子进程被单独拦一次。

### 本地复现（在 Apple Silicon Mac 上）

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[build]"
(cd web && npm ci && npm run build)
bash build-unix.sh && .venv/bin/python scripts/pack_backend.py
(cd desktop/electron && npm ci && npx electron-builder --mac dmg zip --publish never)
bash scripts/verify_macos_app.sh dist/electron 1.23.0-rc1
```

> `build-unix.sh` 与 Linux 共用（原名 `build-linux.sh`，保留为兼容转发）。脚本内只有两处平台分支：`sha256sum` 在 macOS 上不存在（改用 `shasum -a 256`），以及 macOS 上 PyInstaller 跳过 `.ico` 图标（darwin 要求 `.icns`，而 macOS 的后端是纯命令行可执行文件，图标由 Electron 外壳承担）。

### 产物校验

`scripts/verify_macos_app.sh` 逐条断言，CI 上传前必过：

| 检查 | 断言 | 为什么必须查 |
| --- | --- | --- |
| 命名 | `qingci-bot-ce-<ver>-arm64.{dmg,zip}` 存在 | 版本/架构错位会让用户下到别人的包 |
| 架构 | `lipo -archs` 只含 arm64，**拒绝** x86_64 切片 | Intel 切片意味着产物跑不了 |
| 签名 | `codesign -dv` 含 `Signature=adhoc` 且 `codesign --verify --deep --strict` 通过 | 未签名 → 用户看到「已损坏」 |
| 元信息 | `CFBundleIdentifier` = `com.qingcibot.ce`，`LSMinimumSystemVersion` = `13.0` | bundle id 不一致会导致权限/数据目录错乱 |
| 真实下限 | 用 `vtool -show-build` 读主程序与后端的 `minos`，要求**声明值 ≥ 两者** | 声明低于真实下限 → macOS 11/12 用户「装得上、一开就崩」，症状与签名失效几乎一样 |
| 可运行 | 解开 `resources/backend.zip` 跑 `--help`，退出码必须为 0 | 前三项全对但 dylib 缺失/签名失效，只有真跑一次才查得出来 |

> 图标要求 ≥512×512 PNG（electron-builder 据此生成 `.icns`）。仓库里 `build/icon.png` 只有 256×256，另备 `build/icon-1024.png` 专供 macOS。

## 产物命名与校验

发布产物统一为 **小写包名 + 版本 + 架构**，各格式沿用其生态的分隔符约定（deb 用下划线、rpm 用连字符 + release、Windows 用连字符）：

| 产物 | 文件名 | 包内标识 |
| --- | --- | --- |
| 桌面版 deb | `qingci-bot-ce_<ver>_amd64.deb` | `Package: qingci-bot-ce` |
| 桌面版 rpm | `qingci-bot-ce-<ver>-1.x86_64.rpm` | `Name: qingci-bot-ce` |
| 桌面版 Windows 安装向导 | `qingci-bot-ce-<ver>-x64-setup.exe` | 产品名 `Qingci-Bot CE` |
| 桌面版 Windows 便携包 | `qingci-bot-ce-<ver>-x64-portable.zip` | 解压后运行 `Qingci-Bot CE.exe` |
| 服务端 deb | `qingci-bot-ce-server_<ver>_amd64.deb` | `Package: qingci-bot-ce-server` |
| 服务端 rpm | `qingci-bot-ce-server-<ver>-1.x86_64.rpm` | `Name: qingci-bot-ce-server` |
| 桌面版 deb（arm64） | `qingci-bot-ce_<ver>_arm64.deb` | `Architecture: arm64` |
| 桌面版 rpm（arm64） | `qingci-bot-ce-<ver>-1.aarch64.rpm` | `Arch: aarch64` |
| 服务端 deb（arm64） | `qingci-bot-ce-server_<ver>_arm64.deb` | `Architecture: arm64` |
| 服务端 rpm（arm64） | `qingci-bot-ce-server-<ver>-1.aarch64.rpm` | `Arch: aarch64` |
| macOS 安装镜像（arm64） | `qingci-bot-ce-<ver>-arm64.dmg` | bundle id `com.qingcibot.ce` |
| macOS 免安装包（arm64） | `qingci-bot-ce-<ver>-arm64.zip` | 解压后为 `Qingci-Bot CE.app` |
| 校验和 | `SHA256SUMS.txt` | 上述 12 个分发包的 sha256 |

> deb 的架构名是 `arm64`、rpm 是 `aarch64`（两者规范不同），因此 arm64 的 deb 与 rpm 文件名里的架构后缀不一样，这不是笔误。

命名在 `desktop/electron/package.json`（`deb` / `rpm` / `nsis` / `win` 的 `artifactName`、`packageName`）与 `scripts/pack_linux_server.sh` 中定义，并由两处 CI 断言锁定：

- `ci-desktop.yml` 校验 `artifactName` 模板与 `packageName`，防止改回旧的大写命名；
- `release.yml` 的 `linux-deb-rpm` 与 `linux-arm64` 两个 job 共用 `scripts/verify_linux_packages.sh`（架构作为参数：`amd64 x86_64` / `arm64 aarch64`），用 `dpkg-deb -f` / `rpm -qp` 读回包元数据，断言**文件名与包内 Name/Version/Arch 一致**、且每个架构恰好 4 个 Linux 产物。

校验和说明：`dist/backend-manifest.sha256` 是**后端构建清单**（冻结后的可执行文件 + Web UI 入口），由 `build.bat` / `build-unix.sh` 生成，不进 Release；发布包的校验和是 `SHA256SUMS.txt`，由 `release.yml` 的 `checksums` job 生成——直接读取各资产在 GitHub 侧的 sha256 digest（接收上传时已计算），无需把约 1.8 GB 产物下载回来重算，产物缺失或 digest 缺失时该 job 明确失败。`sha256sum -c SHA256SUMS.txt` 可直接校验下载。

本地产物落点：桌面版在 `dist/electron/`，服务端包在 `dist/linux/`；CI 会把桌面版归集到 `dist/linux/` 后统一上传（`Collect desktop packages into dist/linux` 步骤），本地构建无需手动搬。

## 安装冒烟（deb / rpm）

`scripts/smoke_install_packages.sh <包目录> [--only deb|rpm]` 把分发包**真正装进**目标发行版容器（deb → Ubuntu，rpm → Fedora），断言"装得上 → 布局对 → 跑得起来 → 卸得干净"：

| 包 | 断言 |
| --- | --- |
| 服务端 | 冻结二进制可执行 / 系统用户 `qingci-bot` 已创建 / systemd 单元 `ExecStart` 指向安装目录 / 数据与日志目录存在 / `--help` 退出码 0 / **headless 启动后 `/api/bot/health` 返回 200** / `remove` 保留用户实例数据、`purge` 才清 `/var/lib` |
| 桌面 | 可执行文件 + `/usr/bin` 软链可解析 + `.desktop` 条目 / 内嵌 `resources/backend.zip` 存在且**解压后主程序可运行** / `remove` 后安装目录清理 |

为什么桌面也要跑后端：容器内没有 X，桌面壳本身起不来；但内嵌后端是纯 CLI，解压后跑 `--help` 能验证"冻结产物在该发行版上可加载"。**不测这一步桌面包会假绿**——装得上、一启动却拉不起后端。

运行位置（同一脚本，避免两套断言漂移）：

- `ci.yml` 的 `package-smoke` job：只在打包链路输入变化时运行（桌面壳、spec、打包脚本），含构建约 9 分钟；
- `release.yml` 的 `Install smoke` 步骤：**上传前必过**，防止把装不上的包发出去。

四个用例（server/desktop × deb/rpm）**并行执行**——每个用例都要在干净容器里装 240~355 MB 的包再冷启动一次后端，串行累计 20 分钟以上极易被误判成卡死；并行后墙钟 ≈ 最慢那个用例（实测约 70 秒），且每条断言带「用例内已耗时」前缀，卡住时能直接定位。脚本内置硬超时（单用例 900s）与 apt/dnf 网络上限（25s 超时、2 次重试），镜像源不可达时快速失败并给出原因。

本地运行需要容器引擎（docker 或 podman）；SELinux 主机（Fedora / RHEL）上脚本会自动加 `--security-opt label=disable`（否则只读 bind mount 被标签策略拒绝）：

```bash
SMOKE_ENGINE=podman bash scripts/smoke_install_packages.sh dist/linux
# 只测一种格式
SMOKE_ENGINE=podman bash scripts/smoke_install_packages.sh dist/linux --only rpm
# 换目标发行版
UBUNTU_IMAGE=debian:12 SMOKE_ENGINE=podman bash scripts/smoke_install_packages.sh dist/linux --only deb
# 测 arm64 包：在 arm64 机器上直接跑（CONTAINER_PLATFORM 留空 = 宿主原生架构）
bash scripts/smoke_install_packages.sh dist/linux
```

`CONTAINER_PLATFORM` 留空即「宿主原生架构」。在 x64 机器上想测 arm64 包，可以显式设 `CONTAINER_PLATFORM=linux/arm64`（需 binfmt/qemu，CI 不再依赖这条路径）；QEMU 下 apt/dnf 与冷启动更慢，单用例硬超时因此放宽到 1200s。

arm64 包走完全相同的四类断言。CI 里 `linux-arm64` job 跑在原生 `ubuntu-22.04-arm` 上，冒烟也是原生执行，不再经过 QEMU。

### 构建机的 glibc 决定运行时下限

PyInstaller 会把**构建机**的 `libstdc++.so.6` / `libgcc_s.so.1` 打进 `_internal/`。在较新的发行版上构建、到较老的发行版运行，会以这种方式失败：

```
ImportError: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found
  (required by /opt/qingci-bot-ce-server/_internal/libstdc++.so.6)
```

因此 Linux 构建基线固定为**最老的受支持发行版** `ubuntu-22.04`（glibc 2.35）：在老系统上构建，产物在新系统上照样能跑，反之不成立——这是 manylinux 的基本规则。

> **1.22.0 的事故记录**：该版在 `ubuntu-latest`（24.04，glibc 2.39）上构建，产物要求 glibc ≥ 2.38，
> 导致 Ubuntu 22.04（2.35）与 Debian 12（2.36）**装得上但启动即崩**。1.22.1 起基线降到 22.04 修复。
> 该问题是在引入安装冒烟后才被发现的——硬编码的元数据断言看不出来。

当前兼容矩阵（1.22.1 起，构建基线 glibc 2.35）：

| 目标系统 | glibc | 结果 |
| --- | --- | --- |
| Ubuntu 22.04 | 2.35 | ✅ 构建基线，CI 每次打包改动都会实测 |
| Debian 12 | 2.36 | ✅ |
| Ubuntu 24.04 | 2.39 | ✅ |
| Fedora 41 | 2.40 | ✅ |

arm64 产物的下限与 x64 相同：后端在 `ubuntu-22.04-arm` 上构建（glibc 2.35），冒烟也跑在同一个宿主上，两边对齐。

改构建基线时的**四处联动**（漏一处就会出现"测的系统与发的产物不匹配"）：`release.yml` 的 `linux-deb-rpm.runner`、`release.yml` 的 `linux-arm64.runner`（须选同代 arm 镜像，如 `ubuntu-22.04-arm`）、`ci.yml` 的 `package-smoke.runner`、`scripts/smoke_install_packages.sh` 的 `UBUNTU_IMAGE` 默认值。

## 自动发布（GitHub Actions）

打 `vX.Y.Z` tag（或在 Actions 里手动触发 `workflow_dispatch`）即自动构建并上传分发包到同名 GitHub Release，同时构建并推送 Docker 镜像到 `ghcr.io/<repo>`——无需手动上传。流水线见 [`.github/workflows/release.yml`](../.github/workflows/release.yml)，各平台自动联编：
- Windows 桌面（`qingci-bot-ce-<ver>-x64-setup.exe` + `-portable.zip`，`build.bat` + `build-electron.bat`）
- Linux deb / rpm 共 8 个产物：x86_64 与 arm64 各 4 个（`build-unix.sh` + `electron-builder --linux deb rpm` 出桌面版，`scripts/pack_linux_server.sh` 出服务端版；arm64 整条链跑在原生 `ubuntu-22.04-arm` 上）
- macOS 共 2 个产物（仅 Apple Silicon）：`qingci-bot-ce-<ver>-arm64.dmg` + `-arm64.zip`（`macos-15` 构建，electron-builder ad-hoc 签名，上传前过 `scripts/verify_macos_app.sh`）
- Docker 镜像（`ghcr.io`，`linux/amd64` + `linux/arm64` 多架构 manifest；tag：`X.Y.Z` / `X.Y` / `X` / `latest`，`docker/metadata-action` 会剥掉 `v` 前缀）
- `SHA256SUMS.txt`（**全部**构建 job 都成功后由 `checksums` job 从资产 digest 统一生成；共 12 个分发包，少一个即失败）

### 预发布（rc）

tag 版本号含 `rc`（如 `v1.23.0-rc1`）时，Release 自动标记 **Pre-release** 且不替换 Latest；包内元数据版本按 deb/rpm 预发布写法转成 `1.23.0~rc1`（排序在正式版之前，升级方向正确），文件名保持 `-rc1`（GitHub 会把资产名里的 `~` 改写成 `.`）。元数据校验同时接受两种写法。完整约定见 [CONTRIBUTING.md](../CONTRIBUTING.md#预发布rc与正式发布)。
