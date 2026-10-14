# =========================================================
#  插件启动引导 —— .uplugin 的 PostStartupScripts 会执行它
#  作用：把插件 Python 目录加进 sys.path，加载监听模块
# =========================================================

import unreal
import os
import sys


def _get_plugin_python_dir():
    """用 UE API 定位到插件 Python 目录（不依赖 __file__）"""
    try:
        plugins_dir = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_plugins_dir()
        )
        return os.path.normpath(os.path.join(plugins_dir, "WWQY_SendSetting", "Content", "Python"))
    except Exception:
        return None


_dir = _get_plugin_python_dir()

if _dir and os.path.isdir(_dir):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)
    try:
        import auto_material_watcher  # noqa: F401
        unreal.log(f"[Valorant] 插件 Python 已加载: {_dir}")
    except Exception as e:
        unreal.log_error(f"[Valorant] 加载监听模块失败: {e}")
else:
    unreal.log_warning(f"[Valorant] 插件 Python 目录不存在: {_dir}")