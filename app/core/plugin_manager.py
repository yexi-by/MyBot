import inspect
import ast
from collections import defaultdict
from types import UnionType
from typing import Any, Callable, Union, get_args, get_origin, Awaitable, TYPE_CHECKING
import asyncio
from app.models import AllEvent

if TYPE_CHECKING:
    from app.plugins import BasePlugin  # 防循环引用


class PluginController:
    def __init__(
        self,
        plugin_objects: list["BasePlugin"],
    ) -> None:
        self.plugin_objects = plugin_objects
        self.handlers_map: dict[type[AllEvent], list[tuple[Callable, str]]] = (
            defaultdict(list)
        )
        self.internal_listeners: dict[str, list[Callable[..., Awaitable[Any]]]] = (
            defaultdict(
                list
            )  
        )
        self._load_plugins()
        self._auto_detect_deadlocks()

    @staticmethod
    def get_dependency(func: Callable[..., Any]) -> dict[str, type[AllEvent]]:
        sig = inspect.signature(func)
        valid_params = [p for p in sig.parameters.values() if p.name != "self"]
        if len(valid_params) != 1:
            raise ValueError(
                f"插件定义错误: 方法 '{func.__name__}' 必须且只能接受 1 个事件参数。"
            )
        dependencies = {}
        param = valid_params[0]
        if param.annotation is inspect.Parameter.empty:
            raise ValueError(f"错误: 参数 '{param.name}' 缺少类型注解")
        dependencies[param.name] = param.annotation
        return dependencies

    def register_listener(
        self, event_name: str, callback: Callable[..., Awaitable[Any]]
    ) -> None:
        callback_lst = self.internal_listeners[event_name]
        callback_lst.append(callback)

    async def broadcast(self, event_name: str, **kwargs) -> Any:
        listeners = self.internal_listeners.get(event_name, [])
        if not listeners:
            return
        tasks = [func(**kwargs) for func in listeners]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    def _load_plugins(self) -> None:
        for plugin in self.plugin_objects:
            plugin.set_controller(self)  # 将自己注入进插件内部
            dependencies = self.get_dependency(plugin.run)
            param_name, event_type = next(iter(dependencies.items()))
            origin = get_origin(event_type)
            if origin is Union or origin is UnionType:
                args = get_args(event_type)
                for arg in args:
                    self.handlers_map[arg].append((plugin.add_to_queue, param_name))
            else:
                self.handlers_map[event_type].append((plugin.add_to_queue, param_name))

    def _analyze_code_dependencies(self, plugin: "BasePlugin") -> set[str]:
        """读取插件源码,解析AST语法树,找出所有的订阅事件名"""
        dependencies = set()
        try:
            source = inspect.getsource(plugin.__class__)
            tree = ast.parse(source)
        except Exception as e:
            raise RuntimeError(f"源码被加密,亦或是pyc文件,禁止运行,具体错误:{e}")
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
        """构建全自动依赖图并检测闭环"""
        event_listeners = defaultdict(list)
        for p in self.plugin_objects:
            for event_name, _ in p._pending_listeners:
                event_listeners[event_name].append(p.name)
        graph = defaultdict(set)
        for p in self.plugin_objects:
            outgoing_events = self._analyze_code_dependencies(p)
            for event in outgoing_events:
                targets = event_listeners.get(event, [])
                for target in targets:
                    if target != p.name:  # 自己调用自己不算死锁
                        graph[p.name].add(target)
        visited, recursion_stack = set(), set()

        def dfs(node: str, path: list[str]) -> bool:
            """标准DFS找环算法"""
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
            path.pop()
            return False

        for p in self.plugin_objects:
            if p.name not in visited:
                path = []
                if dfs(node=p.name, path=path):
                    chain = " -> ".join(path)
                    raise RuntimeError(f"AST静态源码分析检测到死锁链:{chain}")
