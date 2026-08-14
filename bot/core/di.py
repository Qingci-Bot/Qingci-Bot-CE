"""依赖注入容器 — 轻量级服务注册与解析

借鉴 NoneBot2 的 DI 体系，提供类型安全的服务注册、解析和生命周期管理。
不引入任何第三方 DI 框架，保持零外部依赖。

使用方式：
    # 注册
    container = DIContainer()
    container.register(Database, db_instance)
    container.register_factory(LLMManager, lambda: LLMManager(cfg))
    container.register_singleton(LLMManager, lambda: LLMManager(cfg))  # 懒加载

    # 接口绑定（注册实现类到接口类型）
    container.register_as(IDatabase, db_instance)

    # 解析
    db = container.resolve(Database)
    llm = container.resolve(LLMManager)

    # 自动注入插件
    container.inject(plugin)
"""

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union, cast, get_args, get_origin

logger = logging.getLogger("qingci-bot.di")


class Depends:
    """handler 参数依赖声明

    用于在 Matcher handler 中显式声明依赖，按需解析：
        async def handler(ctx, db: Database = Depends(Database)):
            ...
    """

    def __init__(self, dependency: Any = None, *, use_cache: bool = True):
        self.dependency = dependency
        self.use_cache = use_cache


async def resolve_handler_args(
    func,
    *,
    context,
    bot,
    container,
) -> tuple[list, dict]:
    """解析函数参数，返回 (位置参数, 关键字参数)，供 handler 注入调用

    注入规则（按参数优先级）：
    1. 参数注解为 context 类型，或参数名为 ctx/match → 传入匹配上下文
    2. 参数默认值为 Depends(...) → 按其依赖解析
    3. 参数注解为 bot 类型 → 传入 bot
    4. 参数注解可在容器中解析 → 从容器注入
    5. 其余参数有默认值 → 使用默认值
    6. 其余 → 传入匹配上下文（向后兼容，视作上下文参数）
    """
    sig = inspect.signature(func)
    positional: list = []
    kwargs: dict = {}
    ctx_type = type(context)

    # 命令参数类型化解析（on_command 的 args_schema，供 handler 按名注入）
    parsed_args: dict = {}
    matcher = getattr(context, "matcher", None)
    schema = (getattr(matcher, "meta", None) or {}).get("args_schema") if matcher else None
    if schema:
        parsed_args = _parse_command_args(schema, getattr(context, "args", ""))

    for name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        ann = param.annotation

        # 1. 匹配上下文
        if ann is not inspect.Parameter.empty and ann is ctx_type:
            positional.append(context)
            continue
        if name in ("ctx", "match"):
            positional.append(context)
            continue

        # 2. Depends 显式依赖
        if isinstance(param.default, Depends):
            kwargs[name] = await _resolve_depends(
                param.default, container=container, bot=bot, context=context
            )
            continue

        # 3. bot 实例
        if ann is not inspect.Parameter.empty and _matches_bot(ann, bot):
            kwargs[name] = bot
            continue

        # 4. 容器可解析类型
        if ann is not inspect.Parameter.empty:
            svc = await container.resolve(ann)
            if svc is not None:
                kwargs[name] = svc
                continue

        # 5. 命令参数（args_schema 类型化解析）
        if name in parsed_args:
            kwargs[name] = parsed_args[name]
            continue

        # 6. 默认值
        if param.default is not inspect.Parameter.empty:
            kwargs[name] = param.default
            continue

        # 7. 兜底：视为上下文
        positional.append(context)

    return positional, kwargs


def _parse_command_args(schema: dict, text: str) -> dict:
    """按空白切分命令参数并按 schema 类型转换

    类型转换失败时保留原始字符串，避免参数错误导致 handler 崩溃。
    """
    result: dict = {}
    tokens = text.split()
    for i, (name, typ) in enumerate(schema.items()):
        if i >= len(tokens):
            break
        raw = tokens[i]
        if typ is str:
            result[name] = raw
            continue
        try:
            result[name] = typ(raw)
        except (ValueError, TypeError):
            result[name] = raw
    return result


async def _resolve_depends(dep: Depends, *, container, bot, context) -> Any:
    """解析 Depends 依赖"""
    target = dep.dependency
    if target is None:
        raise RuntimeError("Depends 依赖为空")
    if isinstance(target, type):
        svc = await container.resolve(target)
        if svc is not None:
            return svc
        if _matches_bot(target, bot):
            return bot
        if target is type(context) or isinstance(context, target):
            return context
        raise RuntimeError(f"依赖 {target.__name__} 未注册")
    if callable(target):
        args, kwargs = await resolve_handler_args(
            target, context=context, bot=bot, container=container
        )
        res = target(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res
        return res
    return target


def _matches_bot(ann: Any, bot: Any) -> bool:
    """判断类型注解是否匹配 bot 实例"""
    if ann is type(bot):
        return True
    try:
        return isinstance(bot, ann)
    except TypeError:
        return False


class ServiceLifetime(str, Enum):
    """服务生命周期"""

    SINGLETON = "singleton"  # 单例（默认）：注册后始终返回同一实例
    TRANSIENT = "transient"  # 瞬时：每次 resolve 创建新实例
    SCOPED = "scoped"  # 作用域：同一 scope 内返回同一实例


@dataclass
class ServiceDescriptor:
    """服务描述符"""

    service_type: type
    lifetime: ServiceLifetime = ServiceLifetime.SINGLETON
    instance: Any | None = None  # SINGLETON / SCOPED 实例
    factory: Callable | None = None  # TRANSIENT 工厂
    # 接口绑定：注册时 service_type 是实际类型，但也可通过 bound_types 解析
    bound_types: set[type] = field(default_factory=set)


class DIContainer:
    """轻量级依赖注入容器

    支持三种注册方式：
    - register(service_type, instance)            注册单例实例
    - register_factory(service_type, factory)      注册 TRANSIENT 工厂
    - register_singleton(service_type, factory)    懒加载单例
    - register_as(interface_type, instance)        接口绑定

    支持三种生命周期：
    - SINGLETON: 全局唯一实例
    - TRANSIENT: 每次 resolve 创建新实例
    - SCOPED:    同一 scope 内返回同一实例（跨 resolve 共享）

    支持按类型解析，也支持 Optional 类型提示的注入。
    """

    def __init__(self):
        self._services: dict[type, ServiceDescriptor] = {}
        self._lock = asyncio.Lock()

    # ---- 注册 ----

    async def register(
        self,
        service_type: type,
        instance: Any,
    ) -> None:
        """注册单例服务实例"""
        async with self._lock:
            self._services[service_type] = ServiceDescriptor(
                service_type=service_type,
                lifetime=ServiceLifetime.SINGLETON,
                instance=instance,
            )

    async def register_factory(
        self,
        service_type: type,
        factory: Callable[[], Any],
    ) -> None:
        """注册 TRANSIENT 工厂（每次 resolve 创建新实例）"""
        async with self._lock:
            self._services[service_type] = ServiceDescriptor(
                service_type=service_type,
                lifetime=ServiceLifetime.TRANSIENT,
                factory=factory,
            )

    async def register_singleton(
        self,
        service_type: type,
        factory: Callable[[], Any],
    ) -> None:
        """注册懒加载单例（首次 resolve 时创建，只创建一次）"""
        async with self._lock:
            self._services[service_type] = ServiceDescriptor(
                service_type=service_type,
                lifetime=ServiceLifetime.SINGLETON,
                factory=factory,
                instance=None,
            )

    async def register_as(
        self,
        interface_type: type,
        instance: Any,
    ) -> None:
        """接口绑定：将实例注册为接口类型

        示例：
            container.register_as(IDatabase, db_instance)
            container.resolve(IDatabase)  # -> db_instance

        同时支持按实际类型和接口类型解析。
        """
        inst_type = type(instance)
        async with self._lock:
            # 注册为接口类型
            self._services[interface_type] = ServiceDescriptor(
                service_type=interface_type,
                lifetime=ServiceLifetime.SINGLETON,
                instance=instance,
            )
            # 如果实际类型也已注册，为其添加接口绑定
            if inst_type in self._services:
                self._services[inst_type].bound_types.add(interface_type)

    # ---- 解析 ----

    async def resolve(self, service_type: type) -> Any | None:
        """按类型解析服务"""
        async with self._lock:
            desc = self._services.get(service_type)
            if desc is None:
                # 尝试通过 bound_types 查找
                for sd in self._services.values():
                    if service_type in sd.bound_types:
                        return sd.instance
                return None

            if desc.lifetime == ServiceLifetime.SINGLETON:
                if desc.instance is None and desc.factory is not None:
                    desc.instance = desc.factory()
                return desc.instance

            if desc.lifetime == ServiceLifetime.TRANSIENT:
                if desc.factory is not None:
                    return desc.factory()
                return None

            if desc.lifetime == ServiceLifetime.SCOPED:
                if desc.instance is None and desc.factory is not None:
                    desc.instance = desc.factory()
                return desc.instance

            return None

    async def resolve_required(self, service_type: type) -> Any:
        """解析服务，不存在时抛出异常"""
        result = await self.resolve(service_type)
        if result is None:
            raise RuntimeError(f"服务未注册: {service_type.__name__}")
        return result

    # ---- 注入 ----

    async def inject(self, target: Any, *, skip_missing: bool = True) -> None:
        """自动注入：将容器中的服务按类型注入到目标对象的属性

        扫描目标对象上已声明的类型注解属性，
        若容器中有对应类型的服务则注入，无时跳过（skip_missing=True）
        或抛出异常（skip_missing=False）。

        支持 Optional[X] 类型注解（自动提取内部类型 X）。
        不会覆盖已设置的非 None 属性值。

        Args:
            target: 注入目标（如 PluginBase 实例）
            skip_missing: 未找到服务时是否跳过（默认 True）
        """
        # 收集目标对象及其所有父类的类型注解
        annotations: dict[str, type] = {}
        for cls in type(target).__mro__:
            if hasattr(cls, "__annotations__"):
                annotations.update(cls.__annotations__)

        for attr_name, attr_type in annotations.items():
            if attr_name.startswith("_"):
                continue

            # 跳过已设置非 None 值的属性
            current_value = getattr(target, attr_name, None)
            if current_value is not None:
                continue

            # 处理 Optional[X] -> 提取内部类型 X
            resolved_type = self._unwrap_optional(attr_type)

            async with self._lock:
                if resolved_type not in self._services:
                    if not skip_missing:
                        raise RuntimeError(
                            f"注入失败: 未注册服务 {resolved_type.__name__} "
                            f"(目标 {type(target).__name__}.{attr_name})"
                        )
                    continue

                service = await self._resolve_locked(resolved_type)
                if service is not None:
                    setattr(target, attr_name, service)

    # ---- 同步注册（兼容旧代码） ----

    def register_sync(self, service_type: type, instance: Any) -> None:
        """同步注册单例服务实例（兼容旧代码，内部直接写字典）"""
        self._services[service_type] = ServiceDescriptor(
            service_type=service_type,
            lifetime=ServiceLifetime.SINGLETON,
            instance=instance,
        )

    def inject_sync(self, target: Any, *, skip_missing: bool = True) -> None:
        """同步注入（兼容旧代码）"""
        annotations: dict[str, type] = {}
        for cls in type(target).__mro__:
            if hasattr(cls, "__annotations__"):
                annotations.update(cls.__annotations__)

        for attr_name, attr_type in annotations.items():
            if attr_name.startswith("_"):
                continue

            current_value = getattr(target, attr_name, None)
            if current_value is not None:
                continue

            resolved_type = self._unwrap_optional(attr_type)

            if resolved_type not in self._services:
                if not skip_missing:
                    raise RuntimeError(
                        f"注入失败: 未注册服务 {resolved_type.__name__} "
                        f"(目标 {type(target).__name__}.{attr_name})"
                    )
                continue

            service = self._resolve_sync(resolved_type)
            if service is not None:
                setattr(target, attr_name, service)

    # ---- 内部解析 ----

    async def _resolve_locked(self, service_type: type) -> Any | None:
        """内部解析（调用前需持有锁）"""
        desc = self._services.get(service_type)
        if desc is None:
            return None

        if desc.lifetime == ServiceLifetime.SINGLETON:
            if desc.instance is None and desc.factory is not None:
                desc.instance = desc.factory()
            return desc.instance

        if desc.lifetime == ServiceLifetime.TRANSIENT:
            if desc.factory is not None:
                return desc.factory()
            return None

        if desc.lifetime == ServiceLifetime.SCOPED:
            if desc.instance is None and desc.factory is not None:
                desc.instance = desc.factory()
            return desc.instance

        return None

    def _resolve_sync(self, service_type: type) -> Any | None:
        """同步解析（内部使用）"""
        desc = self._services.get(service_type)
        if desc is None:
            return None

        if desc.lifetime == ServiceLifetime.SINGLETON:
            if desc.instance is None and desc.factory is not None:
                desc.instance = desc.factory()
            return desc.instance

        if desc.lifetime == ServiceLifetime.TRANSIENT:
            if desc.factory is not None:
                return desc.factory()
            return None

        if desc.lifetime == ServiceLifetime.SCOPED:
            if desc.instance is None and desc.factory is not None:
                desc.instance = desc.factory()
            return desc.instance

        return None

    # ---- 查询 ----

    async def is_registered(self, service_type: type) -> bool:
        """检查服务是否已注册"""
        async with self._lock:
            return service_type in self._services

    async def list_services(self) -> list[dict]:
        """列出所有已注册的服务"""
        async with self._lock:
            return [
                {
                    "type": desc.service_type.__name__,
                    "lifetime": desc.lifetime.value,
                    "available": desc.instance is not None or desc.factory is not None,
                }
                for desc in self._services.values()
            ]

    async def clear(self) -> None:
        """清空所有注册"""
        async with self._lock:
            self._services.clear()

    # ---- 工具 ----

    @staticmethod
    def _unwrap_optional(tp: type) -> type:
        """提取 Optional[X] / Union[X, None] 中的 X"""
        origin = get_origin(tp)
        if origin is Union:
            args = [a for a in get_args(tp) if a is not type(None)]
            if len(args) == 1:
                return cast(type, args[0])
        return tp
