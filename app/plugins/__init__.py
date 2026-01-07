import importlib.util
import sys
from operator import attrgetter
from pathlib import Path

from app.utils import logger

from .base import PLUGINS, BasePlugin, Context

# 文件模式常量
PYTHON_FILE_PATTERN = "*.py"
INIT_FILENAME = "__init__.py"
BASE_FILENAME = "base.py"


def load_all_plugins():
    """
    递归加载插件目录下的所有插件模块。

    该函数会扫描当前插件包（app.plugins）及其所有子目录，自动发现并导入所有
    Python 模块文件（排除 __init__.py），实现插件的动态加载。

    工作流程:
        1. 获取插件包的根目录路径
        2. 递归遍历所有 .py 文件（跳过 __init__.py）
        3. 为每个模块文件构建完整的模块名称
        4. 使用 importlib 动态导入模块
        5. 将模块注册到 sys.modules
        6. 记录加载成功的日志信息

    副作用:
        - 修改 sys.modules，添加所有发现的插件模块
        - 触发模块级别的代码执行，包括插件类的注册
        - 输出日志信息到控制台

    注意:
        - 插件类需要继承 BasePlugin 才能被自动注册到 PLUGINS 列表
        - 加载后的插件会根据 priority 属性降序排序
        - 无效的模块规范会被跳过，不会中断加载流程
    """
    current_dir = Path(__file__).parent
    package_name = __name__
    logger.debug(f"开始扫描插件目录: {current_dir}")
    all_py_files = list(current_dir.rglob(PYTHON_FILE_PATTERN))
    logger.debug(f"发现 {len(all_py_files)} 个 .py 文件: {[f.name for f in all_py_files]}")
    
    for file_path in all_py_files:
        if file_path.name == INIT_FILENAME:
            continue
        if file_path.name == BASE_FILENAME:
            logger.debug(f"跳过 {BASE_FILENAME}（已通过直接导入加载）")
            continue
        relative_path = file_path.relative_to(current_dir)
        module_rel_name = relative_path.with_suffix("").as_posix().replace("/", ".")
        full_module_name = f"{package_name}.{module_rel_name}"
        
        # 检查模块是否已经被加载
        if full_module_name in sys.modules:
            logger.debug(f"模块 {full_module_name} 已存在于 sys.modules，跳过")
            continue
            
        spec = importlib.util.spec_from_file_location(full_module_name, file_path)
        if not spec or not spec.loader:
            logger.warning(f"无法获取模块规范: {full_module_name}")
            continue
        try:
            module = importlib.util.module_from_spec(spec)
            sys.modules[full_module_name] = module
            spec.loader.exec_module(module)
            logger.info(f" [加载成功] {full_module_name} ({file_path.name})")
        except Exception as e:
            logger.error(f" [加载失败] {full_module_name}: {e}")


load_all_plugins()

logger.info(f"插件加载完成，共加载 {len(PLUGINS)} 个插件: {[p.name for p in PLUGINS]}")

PLUGINS.sort(key=attrgetter("priority"), reverse=True)

__all__ = ["PLUGINS", "BasePlugin", "Context"]
