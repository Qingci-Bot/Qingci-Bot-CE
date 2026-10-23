"""桌面端路径一致性测试

安装器（NSIS）和应用（Electron 主进程）必须对「数据目录」用同一套路径约定，
否则安装时选的数据目录应用读不到，数据会落到另一个隐藏位置。

曾出现过：安装器写 `%APPDATA%\\Qingci-Bot CE\\datadir.txt`，应用却用
`app.getPath("userData")`（取 package.json 的 name = qingci-bot-ce-desktop），
两个目录名不一致 → 用户选了目录但从未被使用。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_JS = ROOT / "desktop" / "electron" / "src" / "main.js"
INSTALLER_NSH = ROOT / "desktop" / "electron" / "build" / "installer.nsh"


def _main_js() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


def _installer() -> str:
    return INSTALLER_NSH.read_text(encoding="utf-8-sig")


def test_product_dir_name_is_consistent():
    """应用使用的产品目录名必须与安装器写 datadir.txt 的目录名一致"""
    m = re.search(r'const PRODUCT_DIR_NAME = "([^"]+)"', _main_js())
    assert m, "main.js 未定义 PRODUCT_DIR_NAME"
    product_dir = m.group(1)

    # 安装器：FileOpen ... "$APPDATA\<dir>\datadir.txt"
    n = re.search(r'FileOpen \$0 "\$APPDATA\\([^"]+)\\datadir\.txt"', _installer())
    assert n, "installer.nsh 未写入 datadir.txt"
    installer_dir = n.group(1)

    assert product_dir == installer_dir, (
        f"目录名不一致：应用={product_dir!r} 安装器={installer_dir!r}；"
        "会导致安装时选择的数据目录读不到"
    )


def test_default_data_home_matches_installer():
    """应用未读到 datadir.txt 时的默认目录，必须与安装器默认值一致"""
    m = re.search(r'const PRODUCT_DIR_NAME = "([^"]+)"', _main_js())
    assert m
    product_dir = m.group(1)

    # 安装器默认：$LOCALAPPDATA\<dir>\data
    n = re.search(r'!define QINGCI_DATADIR_DEFAULT "\$LOCALAPPDATA\\([^"]+)\\data"', _installer())
    assert n, "installer.nsh 未定义默认数据目录"
    assert n.group(1) == product_dir

    # 应用侧默认值由 defaultDataHome() 拼出：LOCALAPPDATA/<PRODUCT_DIR_NAME>/data
    js = _main_js()
    assert "process.env.LOCALAPPDATA" in js
    assert 'path.join(localAppData, PRODUCT_DIR_NAME, "data")' in js


def test_app_reads_datadir_from_appdata_not_userdata():
    """datadir.txt 必须从 %APPDATA%\\<产品目录> 读取，不能用 userData

    userData 取的是 package.json 的 name（qingci-bot-ce-desktop），与产品目录不同名。
    """
    js = _main_js()
    assert 'app.getPath("appData")' in js
    # 数据目录记录路径不应再依赖 userData
    assert 'path.join(app.getPath("userData"), "datadir.txt")' not in js


def test_installer_avoids_relative_jumps():
    """NSIS 脚本里禁止用相对跳转（IfFileExists ... +N）

    曾踩坑：`IfFileExists "$dir" +2 0` 本意是「存在则继续」，
    但 +2 从 IfFileExists 起算正好落到后面的 Abort —— 目录明明存在，
    点「安装」却静默中止（表现为按钮无反应）。改用 LogicLib 的
    ${If} / ${IfNot} 后语义明确，不再依赖数行数。
    """
    text = _installer()
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\bIfFileExists\b[^\n]*\s\+\d", line)
    ]
    assert not offenders, "发现相对跳转，请改用 LogicLib 条件块：\n" + "\n".join(offenders)
