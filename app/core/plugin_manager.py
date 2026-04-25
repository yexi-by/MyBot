"""插件路由、内部广播与静态依赖检查。"""

import ast
import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast, get_args, get_type_hints

from app.models import AllEvent

if TYPE_CHECKING:
    from app.plugins.base import BasePlugin

type EventHandler = Callable[..., Awaitable[bool]]
type InternalListener = Callable[..., Awaitable[object]]


class PluginController:
    """管理插件实例、事件路由与插件内部广播。"""

    def __init__(
        self,
        plugin_objects: list["BasePlugin[AllEvent]"],
    ) -> None:
        """保存插件实例并构建事件路由。"""
        self.plugin_objects: list["BasePlugin[AllEvent]"] = plugin_objects
        self.handlers_map: dict[
            type[AllEvent], list[tuple[EventHandler, str]]
        ] = defaultdict(list)
        self.internal_listeners: dict[str, list[InternalListener]] = defaultdict(list)
        self._load_plugins()
        self._auto_detect_deadlocks()

    @staticmethod
    def get_dependency(func: EventHandler) -> tuple[str, object]:
        """读取插件 run 方法的事件类型依赖。"""
        sig = inspect.signature(func)
        valid_params = [p for p in sig.parameters.values() if p.name != "self"]
        if len(valid_params) != 1:
            raise ValueError(
                f"插件定义错误: 方法 '{func.__name__}' 必须且只能接受 1 个事件参数。"
            )
        param = valid_params[0]
        hints = get_type_hints(func)
        annotation: object | None = hints.get(param.name)
        if annotation is None:
            raise ValueError(f"错误: 参数 '{param.name}' 缺少类型注解")
        return param.name, annotation

    @staticmethod
    def _resolve_event_types(annotation: object) -> tuple[type[AllEvent], ...]:
        """把单个事件类型或联合事件类型收窄为事件类型元组。"""
        raw_types = get_args(annotation)
        if not raw_types:
            raw_types = (annotation,)
        event_types: list[type[AllEvent]] = []
        for raw_type in raw_types:
            if not isinstance(raw_type, type):
                raise TypeError(f"插件事件类型必须是类，实际为: {raw_type!r}")
            event_types.append(cast(type[AllEvent], raw_type))
        return tuple(event_types)

    def register_listener(
        self, event_name: str, callback: InternalListener
    ) -> None:
        """注册插件内部广播监听器。"""
        callback_lst = self.internal_listeners[event_name]
        callback_lst.append(callback)

    async def broadcast(
        self, event_name: str, kwargs: dict[str, object]
    ) -> list[object | BaseException] | None:
        """广播插件内部事件。"""
        listeners = self.internal_listeners.get(event_name, [])
        if not listeners:
            return None
        tasks = [func(**kwargs) for func in listeners]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    def _load_plugins(self) -> None:
        """载入插件并建立事件类型到插件队列的映射。"""
        for plugin in self.plugin_objects:
            plugin.set_controller(self)
            param_name, annotation = self.get_dependency(plugin.run)
            for event_type in self._resolve_event_types(annotation):
                self.handlers_map[event_type].append((plugin.add_to_queue, param_name))

    def _analyze_code_dependencies(self, plugin: "BasePlugin[AllEvent]") -> set[str]:
        """读取插件源码并解析 emit 调用依赖。"""
        dependencies: set[str] = set()
        try:
            source = inspect.getsource(plugin.__class__)
            tree = ast.parse(source)
        except Exception as exc:
            raise RuntimeError(f"源码被加密，或为 pyc 文件，禁止运行。错误: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if not isinstance(func, ast.Attribute) or func.attr != "emit":
                continue
            if not node.value.args or not isinstance(node.value.args[0], ast.Constant):
                continue
            event_name = node.value.args[0].value
            if not isinstance(event_name, str):
                continue
            dependencies.add(event_name)
        return dependencies

    def _auto_detect_deadlocks(self) -> None:
        """构建插件内部事件依赖图并检测闭环。"""
        event_listeners: dict[str, list[str]] = defaultdict(list)
        for plugin in self.plugin_objects:
            for event_name, _ in plugin.pending_listeners():
                event_listeners[event_name].append(plugin.name)

        graph: dict[str, set[str]] = defaultdict(set)
        for plugin in self.plugin_objects:
            outgoing_events = self._analyze_code_dependencies(plugin)
            for event in outgoing_events:
                targets = event_listeners.get(event, [])
                for target in targets:
                    if target != plugin.name:
                        graph[plugin.name].add(target)

        visited: set[str] = set()
        recursion_stack: set[str] = set()

        def dfs(node: str, path: list[str]) -> bool:
            """使用 DFS 检测依赖图中的环。"""
            visited.add(node)
            recursion_stack.add(node)
            path.append(node)
            for neighbor in graph[node]:
                if neighbor not in visited:
                    if dfs(node=neighbor, path=path):
                        return True
                elif neighbor in recursion_stack:
                    return True
            recursion_stack.remove(node)
            _ = path.pop()
            return False

        for plugin in self.plugin_objects:
            if plugin.name not in visited:
                path: list[str] = []
                if dfs(node=plugin.name, path=path):
                    chain = " -> ".join(path)
                    raise RuntimeError(f"AST 静态源码分析检测到死锁链: {chain}")
