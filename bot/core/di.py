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
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Union, get_origin, get_args

logger = logging.getLogger("qingci-bot.di")


class ServiceLifetime(str, Enum):
    """服务生命周期"""
    SINGLETON = "singleton"   # 单例（默认）：注册后始终返回同一实例
    TRANSIENT = "transient"   # 瞬时：每次 resolve 创建新实例
    SCOPED = "scoped"         # 作用域：同一 scope 内返回同一实例


@dataclass
class ServiceDescriptor:
    """服务描述符"""
    service_type: type
    lifetime: ServiceLifetime = ServiceLifetime.SINGLETON
    instance: Optional[Any] = None          # SINGLETON / SCOPED 实例
    factory: Optional[Callable] = None       # TRANSIENT 工厂
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

    async def resolve(self, service_type: type) -> Optional[Any]:
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

    async def _resolve_locked(self, service_type: type) -> Optional[Any]:
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

    def _resolve_sync(self, service_type: type) -> Optional[Any]:
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
                    "available": desc.instance is not None
                        or desc.factory is not None,
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
                return args[0]
        return tp