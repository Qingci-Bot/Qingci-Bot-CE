# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE

"""DI 容器契约：async / sync 双轨等价与生命周期语义

P1「DI 双轨合一」的回归网。单轨核心重构后，两条路径必须给出相同结果；
接口绑定（register_as）此前只在 async 路径生效，现统一为一致行为。
"""

import pytest

from bot.core.di import DIContainer


class Svc:
    pass


class IFace:
    pass


class Target:
    svc: Svc | None = None
    missing: str | None = None


@pytest.fixture
def di() -> DIContainer:
    return DIContainer()


async def test_async_and_sync_register_resolve_equivalent(di):
    inst = Svc()
    await di.register(Svc, inst)
    assert await di.resolve(Svc) is inst
    assert di.resolve_sync(Svc) is inst


async def test_register_sync_then_async_resolve(di):
    """启动期用 register_sync 装配，运行期用 await resolve 读取"""
    inst = Svc()
    di.register_sync(Svc, inst)
    assert await di.resolve(Svc) is inst
    assert di.resolve_sync(Svc) is inst


async def test_transient_factory_creates_each_time(di):
    calls: list[int] = []
    await di.register_factory(Svc, lambda: calls.append(1) or Svc())
    first = await di.resolve(Svc)
    second = di.resolve_sync(Svc)
    assert first is not second
    assert len(calls) == 2


async def test_lazy_singleton_created_once_across_tracks(di):
    calls: list[int] = []
    await di.register_singleton(Svc, lambda: calls.append(1) or Svc())
    first = await di.resolve(Svc)
    assert di.resolve_sync(Svc) is first
    assert len(calls) == 1


async def test_interface_binding_resolves_on_both_tracks(di):
    inst = Svc()
    await di.register(Svc, inst)
    await di.register_as(IFace, inst)
    assert await di.resolve(IFace) is inst
    assert di.resolve_sync(IFace) is inst


@pytest.mark.parametrize("sync", [False, True])
async def test_inject_both_tracks_equivalent(di, sync):
    inst = Svc()
    di.register_sync(Svc, inst)
    target = Target()
    if sync:
        di.inject_sync(target)
    else:
        await di.inject(target)
    assert target.svc is inst
    assert target.missing is None


@pytest.mark.parametrize("sync", [False, True])
async def test_inject_raises_when_missing_and_not_skipped(di, sync):
    target = Target()
    with pytest.raises(RuntimeError, match="注入失败"):
        if sync:
            di.inject_sync(target, skip_missing=False)
        else:
            await di.inject(target, skip_missing=False)


async def test_inject_does_not_overwrite_existing_value(di):
    di.register_sync(Svc, Svc())
    target = Target()
    existing = Svc()
    target.svc = existing
    await di.inject(target)
    assert target.svc is existing


async def test_resolve_required_raises_when_unregistered(di):
    with pytest.raises(RuntimeError, match="服务未注册"):
        await di.resolve_required(Svc)


async def test_query_and_clear(di):
    di.register_sync(Svc, Svc())
    assert await di.is_registered(Svc)
    assert any(s["type"] == "Svc" for s in await di.list_services())
    await di.clear()
    assert di.resolve_sync(Svc) is None
