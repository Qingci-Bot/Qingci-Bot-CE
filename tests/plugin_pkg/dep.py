# Copyright (C) 2026 Zhou Zhe (aka luoqingci) and Qingci-Bot contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see LICENSE

"""依赖插件：被 dep_plugin 声明 require"""

from bot.plugin.base import PluginBase


class DepPlugin(PluginBase):
    name = "dep"
    version = "1.0.0"

    async def on_load(self):
        pass

    async def on_unload(self):
        pass
