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

两款包的共同前提是先产出 Python 后端 onedir（`build-linux.sh`，对应 Windows 的 `build.bat`，输出 `dist/qingci-bot-ce/`）。

### 1. 桌面版（electron-builder）

```bash
# 先决条件：.venv + [build] 依赖、Node.js 22+、系统需 rpm 与 fakeroot（生成 rpm 依赖）
./build-linux.sh
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

两种架构的构建方式不同，原因是 PyInstaller 与 Electron 的可交叉性不同：

| 部件 | 交叉编译 | 做法 |
| --- | --- | --- |
| Python 后端（PyInstaller） | ❌ 不支持 | 在 **ubuntu:22.04 arm64 容器**里原生构建（x64 机器上靠 QEMU 模拟） |
| Electron 桌面壳 | ✅ 支持 | 在 x64 宿主上 `electron-builder --linux deb rpm --arm64`，自动下载 arm64 版 Electron |
| 服务端包（dpkg-deb / rpmbuild） | — | 在容器内执行 `pack_linux_server.sh`，`uname -m` 给出 aarch64，架构字段自动正确 |

```bash
# 本地复现（需要 docker/podman + binfmt；产物落在 dist/linux，与 x64 产物同名规则）
docker run --rm --platform linux/arm64 -v "$PWD":/src -v "$PWD/dist/linux":/out \
  ubuntu:22.04 bash -c '
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential xz-utils file dpkg-dev rpm
    git clone --quiet --local /src /build && cp -r /src/web/dist /build/web/dist && cd /build
    curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH"
    uv python install 3.12 && uv venv --python 3.12
    uv pip install --python .venv/bin/python -e ".[build]"
    bash build-linux.sh && python3 scripts/pack_backend.py
    bash scripts/pack_linux_server.sh --version 1.22.4 --out /out
    cp -f dist/backend.zip /out/backend.zip'
cp dist/linux/backend.zip dist/backend.zip
cd desktop/electron && npx electron-builder --linux deb rpm --arm64 --publish never
```

> **为什么不在 GitHub 的 arm64 runner 上直接构建**：托管 arm64 runner 只有 `ubuntu-24.04`（glibc 2.39），
> 在上面构建出的后端会要求 glibc ≥ 2.39，Debian 12 / Ubuntu 22.04 装得上却起不来（1.22.0 在 x64 上
> 踩过同一个坑）。容器把基线钉回 22.04（glibc 2.35），与 x64 产物保持一致的下限。
>
> **为什么容器里要重新 `git clone`**：宿主机工作区带着 x64 的 `.venv` 与 `dist/`，而 `build-linux.sh`
> 会优先复用 `.venv/bin/python`——直接把仓库挂进容器，等于拿 x64 解释器当 arm64 构建环境。

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
| 校验和 | `SHA256SUMS.txt` | 上述 10 个分发包的 sha256 |

> deb 的架构名是 `arm64`、rpm 是 `aarch64`（两者规范不同），因此 arm64 的 deb 与 rpm 文件名里的架构后缀不一样，这不是笔误。

命名在 `desktop/electron/package.json`（`deb` / `rpm` / `nsis` / `win` 的 `artifactName`、`packageName`）与 `scripts/pack_linux_server.sh` 中定义，并由两处 CI 断言锁定：

- `ci-desktop.yml` 校验 `artifactName` 模板与 `packageName`，防止改回旧的大写命名；
- `release.yml` 的 `linux-deb-rpm` 与 `linux-arm64` 两个 job 共用 `scripts/verify_linux_packages.sh`（架构作为参数：`amd64 x86_64` / `arm64 aarch64`），用 `dpkg-deb -f` / `rpm -qp` 读回包元数据，断言**文件名与包内 Name/Version/Arch 一致**、且每个架构恰好 4 个 Linux 产物。

校验和说明：`dist/backend-manifest.sha256` 是**后端构建清单**（冻结后的可执行文件 + Web UI 入口），由 `build.bat` / `build-linux.sh` 生成，不进 Release；发布包的校验和是 `SHA256SUMS.txt`，由 `release.yml` 的 `checksums` job 生成——直接读取各资产在 GitHub 侧的 sha256 digest（接收上传时已计算），无需把约 1.8 GB 产物下载回来重算，产物缺失或 digest 缺失时该 job 明确失败。`sha256sum -c SHA256SUMS.txt` 可直接校验下载。

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
# 测 arm64 包（需 binfmt/qemu，CI 里由 docker/setup-qemu-action 提供）
CONTAINER_PLATFORM=linux/arm64 bash scripts/smoke_install_packages.sh dist/linux
```

arm64 包走完全相同的四类断言，只把容器平台换成 `linux/arm64`；QEMU 下 apt/dnf 与冷启动更慢，单用例硬超时因此放宽到 1200s。

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

arm64 产物的下限与 x64 相同：后端在 `ubuntu:22.04` 的 arm64 容器里构建（glibc 2.35），冒烟同样跑 22.04 容器，两边对齐。

改构建基线时的**四处联动**（漏一处就会出现"测的系统与发的产物不匹配"）：`release.yml` 的 `linux-deb-rpm.runner`、`release.yml` 的 `linux-arm64` 容器镜像、`ci.yml` 的 `package-smoke.runner`、`scripts/smoke_install_packages.sh` 的 `UBUNTU_IMAGE` 默认值。

## 自动发布（GitHub Actions）

打 `vX.Y.Z` tag（或在 Actions 里手动触发 `workflow_dispatch`）即自动构建并上传分发包到同名 GitHub Release，同时构建并推送 Docker 镜像到 `ghcr.io/<repo>`——无需手动上传。流水线见 [`.github/workflows/release.yml`](../.github/workflows/release.yml)，各平台自动联编：
- Windows 桌面（`qingci-bot-ce-<ver>-x64-setup.exe` + `-portable.zip`，`build.bat` + `build-electron.bat`）
- Linux deb / rpm 共 8 个产物：x86_64 与 arm64 各 4 个（`build-linux.sh` + `electron-builder --linux deb rpm [--arm64]` 出桌面版，`scripts/pack_linux_server.sh` 出服务端版；arm64 后端在 `ubuntu:22.04` arm64 容器里构建）
- Docker 镜像（`ghcr.io`，`linux/amd64` + `linux/arm64` 多架构 manifest）
- `SHA256SUMS.txt`（两个构建 job 都成功后由 `checksums` job 从资产 digest 统一生成）
- Docker 镜像（tag：`X.Y.Z` / `X.Y` / `X` / `latest`；`docker/metadata-action` 会剥掉 `v` 前缀）

### 预发布（rc）

tag 版本号含 `rc`（如 `v1.23.0-rc1`）时，Release 自动标记 **Pre-release** 且不替换 Latest；包内元数据版本按 deb/rpm 预发布写法转成 `1.23.0~rc1`（排序在正式版之前，升级方向正确），文件名保持 `-rc1`（GitHub 会把资产名里的 `~` 改写成 `.`）。元数据校验同时接受两种写法。完整约定见 [CONTRIBUTING.md](../CONTRIBUTING.md#预发布rc与正式发布)。
