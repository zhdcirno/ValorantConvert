# =========================================================
#  auto_material_watcher.py
#  资产监听模块 —— 可被 bootstrap.py 或 EUW 按钮调用
#
#  外部接口：
#    start() / stop() / toggle() / is_running() / status()
#    start_overview() / stop_overview() / toggle_overview() / is_overview_running()
#    run_once() / run_overview_once()
#    suppress(seconds) / suppress_overview(seconds)
#    get_active_skin() — 最近导入的皮肤名（供 create_overview.resolve_skin）
#
#  目录约定：本文件、batch_material_setup.py、create_overview.py 同目录
# =========================================================

import unreal
import os
import sys


# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────

WATCH_PATHS = [
    "/Game/Gun/",
    "/Game/Charm/",
]

# 预览地图只关心 Gun 下的新皮肤目录（Charm 不驱动 overview）
GUN_WATCH_PATH = "/Game/Gun/"   # ★ 尾斜杠必须保留：_snapshot_subdirs 的
                                #   pkg[len(path):] 切分依赖它

# 非皮肤目录黑名单（产物目录）。watcher 与 create_overview 共用此约定；
# create_overview 侧 import 失败时自带同名兜底
SKIN_DIR_BLACKLIST = ("map",)

DEBOUNCE_SECONDS = 3.0
COOLDOWN_SECONDS = 3.0
AUTO_RUN = True

# 插件相对路径（用于第二个查找策略）
PLUGIN_PYTHON_RELATIVE = os.path.join(
    "WWQY_SendSetting", "Content", "Python"
)
TARGET_SCRIPT_NAME = "batch_material_setup.py"

# ─── 自动预览地图配置 ───
OVERVIEW_SCRIPT_NAME = "create_overview.py"
OVERVIEW_DEBOUNCE_SECONDS = 5.0   # 材质处理完后等这么久再建地图
OVERVIEW_COOLDOWN_SECONDS = 15.0  # 建完地图后的冷却，避免死循环


# ─────────────────────────────────────────────
# 全局状态
# ─────────────────────────────────────────────

_GLOBAL_KEY = "_auto_material_watcher_state"

if _GLOBAL_KEY not in sys.modules:
    sys.modules[_GLOBAL_KEY] = {
        # ── 材质监听 ──
        "tick_handle": None,
        "running": False,
        "last_count": None,
        "pending": False,
        "countdown": 0.0,
        "material_script": None,
        "executing": False,
        "cooldown": 0.0,
        # ── 预览地图监听 ──
        "overview_running": False,
        "overview_script": None,
        "overview_pending": False,
        "overview_countdown": 0.0,
        "overview_executing": False,
        "overview_cooldown": 0.0,
        # ── 皮肤目录检测（供 create_overview.resolve_skin 使用）──
        "subdir_snapshot": {},       # {"/Game/Gun/": set(子目录名), ...}
        "last_changed_skin": None,   # 最近一次"新增子目录"的名字（仅 Gun 路径）
    }

_state = sys.modules[_GLOBAL_KEY]

# 兼容旧状态
for k, v in (
    ("executing", False),
    ("cooldown", 0.0),
    ("overview_running", False),
    ("overview_script", None),
    ("overview_pending", False),
    ("overview_countdown", 0.0),
    ("overview_executing", False),
    ("overview_cooldown", 0.0),
    ("subdir_snapshot", {}),
    ("last_changed_skin", None),
):
    if k not in _state:
        _state[k] = v


# ─────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────

def log(msg):
    unreal.log(f"[自动材质] {msg}")


def log_warn(msg):
    unreal.log_warning(f"[自动材质] {msg}")


def _get_my_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return None


def _get_plugin_python_dir():
    try:
        plugins_dir = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_plugins_dir()
        )
        return os.path.normpath(os.path.join(plugins_dir, PLUGIN_PYTHON_RELATIVE))
    except Exception:
        return None


def _search_project_for_script(target_name):
    try:
        project_root = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_dir()
        )
    except Exception:
        return None
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs
                   if d not in ("Intermediate", "Binaries", "Saved", ".git")]
        if target_name in files:
            return os.path.join(root, target_name)
    return None


def _find_script_by_name(script_name):
    """按优先级查找：同目录 → 插件 Python 目录 → 全项目搜索"""
    my_dir = _get_my_dir()
    if my_dir:
        p = os.path.join(my_dir, script_name)
        if os.path.isfile(p):
            return p

    plugin_dir = _get_plugin_python_dir()
    if plugin_dir:
        p = os.path.join(plugin_dir, script_name)
        if os.path.isfile(p):
            return p

    return _search_project_for_script(script_name)


def _ensure_script_path():
    if _state.get("material_script"):
        return
    _state["material_script"] = _find_script_by_name(TARGET_SCRIPT_NAME)


def _ensure_overview_script_path():
    if _state.get("overview_script"):
        return
    _state["overview_script"] = _find_script_by_name(OVERVIEW_SCRIPT_NAME)


# ─────────────────────────────────────────────
# 扫描
# ─────────────────────────────────────────────

def _count_assets():
    try:
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        total = 0
        for path in WATCH_PATHS:
            try:
                assets = registry.get_assets_by_path(path, recursive=True)
                total += len(assets)
            except Exception:
                continue
        return total
    except Exception:
        return 0


def _count_per_path():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    result = {}
    for path in WATCH_PATHS:
        try:
            assets = registry.get_assets_by_path(path, recursive=True)
            result[path] = len(assets)
        except Exception:
            result[path] = 0
    return result


# ─────────────────────────────────────────────
# 皮肤目录检测（P0-1 · 供 create_overview.resolve_skin 使用）
# ─────────────────────────────────────────────

def _disk_subdirs(path):
    """os.listdir 枚举 {Content}/ 下对应目录的一级子目录；失败返回 None"""
    try:
        leaf = path.strip("/").split("/")[-1]  # "Gun" / "Charm"
        content_dir = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_content_dir()
        )
        root = os.path.join(content_dir, leaf)
        if os.path.isdir(root):
            return {n for n in os.listdir(root)
                    if os.path.isdir(os.path.join(root, n))}
    except Exception:
        pass
    return None


def _snapshot_subdirs():
    """各监听路径的一级子目录名集合快照

    枚举方式（军师意见③）：os.listdir 优先（快，start() 基线采样无感），
    失败时回退 AssetRegistry（与编辑器视图一致，但全扫较贵）。
    """
    registry = None
    result = {}
    for path in WATCH_PATHS:
        names = _disk_subdirs(path)
        if names is None:
            names = set()
            try:
                if registry is None:
                    registry = unreal.AssetRegistryHelpers.get_asset_registry()
                assets = registry.get_assets_by_path(path, recursive=True)
                for a in assets:
                    try:
                        pkg = str(a.package_name)  # 如 /Game/Gun/InnerRage/Mesh/xxx
                    except Exception:
                        continue
                    if pkg.startswith(path):
                        top = pkg[len(path):].split("/", 1)[0]
                        if top:
                            names.add(top)
            except Exception:
                pass
        result[path] = names
    return result


def _pick_newest_dir(names):
    """多个新增目录时取谁（军师意见①，规则写死）：
    磁盘 mtime 最新优先；mtime 相同则字典序取末位。"""
    try:
        content_dir = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_content_dir()
        )
        gun_root = os.path.join(content_dir, "Gun")
    except Exception:
        gun_root = None
    best_name, best_key = None, (-1.0, "")
    for n in names:
        m = -1.0
        if gun_root:
            try:
                m = os.path.getmtime(os.path.join(gun_root, n))
            except OSError:
                m = -1.0
        key = (m, n)
        if key > best_key:
            best_key, best_name = key, n
    return best_name


def _detect_new_skin_dirs():
    """对比上次子目录名集合快照，把 Gun 下新增的子目录记为 last_changed_skin

    ★ 只关心"新目录出现"，不关心已有目录内资产数涨跌——
      避免 batch_material_setup 建 MI_ 触发自己（军师审阅意见 A）。
    ★ 只在顶层总数变化时被调用（军师审阅意见 B），常驻开销与现状一致。
    ★ last_changed_skin 存纯名（如 "InnerRage"，不含路径），
      create_overview 侧直接 f"{GUN_ROOT}/{skin}" 拼路径（军师意见⑤）。
    """
    try:
        snap = _snapshot_subdirs()
    except Exception as e:
        log_warn(f"子目录快照失败: {e}")
        return

    old = _state.get("subdir_snapshot") or {}
    newest = None  # ★ 显式初始化：Gun 无新增而 Charm 有新增时不得残留旧值

    for path, names in snap.items():
        prev = old.get(path) or set()
        added = names - prev
        # 黑名单目录（如顶层 Map/）永远不算新皮肤
        added = {n for n in added if n.lower() not in SKIN_DIR_BLACKLIST}
        if not added:
            continue
        # ★ 有意只认 Gun 路径的新增目录（军师意见②定性：设计，非遗漏）——
        #   Charm 的新目录语义是"新挂饰"，预览地图不消费它；
        #   未来做挂饰预览地图时在此处一并扩展。
        if path == GUN_WATCH_PATH:
            newest = _pick_newest_dir(added)

    _state["subdir_snapshot"] = snap
    if newest:
        _state["last_changed_skin"] = newest
        log(f"检测到新皮肤目录: {newest}")


def get_active_skin():
    """对外接口：返回最近导入的皮肤纯名（如 "InnerRage"），无效时返回 None

    ★ 返回皮肤名字符串或 None，不是 bool（军师意见④）；调用方判 `if skin:`。
    有效性：本 UE 会话内至少完成过一次子目录快照（快照非空），
    避免读到无基线的残留值（军师审阅意见 C）。
    """
    if _state.get("subdir_snapshot"):
        return _state.get("last_changed_skin")
    return None


# ─────────────────────────────────────────────
# Tick 注册 / 注销
# ─────────────────────────────────────────────

def _ensure_tick_registered():
    if _state.get("tick_handle") is not None:
        return True
    try:
        _state["tick_handle"] = unreal.register_slate_post_tick_callback(_tick_callback)
        return True
    except Exception as e:
        log_warn(f"注册 tick 失败: {e}")
        return False


def _maybe_unregister_tick():
    if _state.get("running") or _state.get("overview_running"):
        return
    handle = _state.get("tick_handle")
    if handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception as e:
            log_warn(f"注销 tick 失败: {e}")
        _state["tick_handle"] = None


# ─────────────────────────────────────────────
# 执行材质脚本
# ─────────────────────────────────────────────

def _run_material_script(trigger_overview=True):
    log("触发自动材质脚本")
    _state["executing"] = True
    try:
        _ensure_script_path()
        script = _state.get("material_script")
        if not script:
            log_warn(f"未找到 {TARGET_SCRIPT_NAME}")
            return
        try:
            with open(script, "r", encoding="utf-8") as f:
                code = f.read()
            g = {"__name__": "__main__", "__file__": script}
            exec(compile(code, script, "exec"), g)
            log("材质脚本执行完成")
        except Exception as e:
            import traceback
            traceback.print_exc()
            log_warn(f"材质脚本执行失败: {e}")
    finally:
        _state["last_count"] = None
        _state["pending"] = False
        _state["executing"] = False
        _state["cooldown"] = COOLDOWN_SECONDS

        # ★ 只有自动触发才联动 overview（手动 run_once 不触发，解耦决策）
        #   且必须自动预览地图监听本身已开启（overview_running）才倒计时
        if trigger_overview and _state.get("overview_running"):
            _state["overview_pending"] = True
            _state["overview_countdown"] = OVERVIEW_DEBOUNCE_SECONDS
            log(f"材质处理完成 → {OVERVIEW_DEBOUNCE_SECONDS:.0f}s 后创建预览地图")


# ─────────────────────────────────────────────
# 执行预览地图脚本
# ─────────────────────────────────────────────

def _run_overview_script():
    log("触发创建预览地图")
    _state["overview_executing"] = True

    # ★ 关键：抑制材质监听，避免建关卡产生的新资产反向触发材质
    _state["cooldown"] = max(_state.get("cooldown", 0.0), 60.0)
    _state["pending"] = False
    _state["countdown"] = 0.0
    _state["last_count"] = None  # 冷却结束后重新采样

    try:
        _ensure_overview_script_path()
        script = _state.get("overview_script")
        if not script:
            log_warn(f"未找到 {OVERVIEW_SCRIPT_NAME}")
            return
        try:
            with open(script, "r", encoding="utf-8") as f:
                code = f.read()
            g = {"__name__": "__main__", "__file__": script}
            exec(compile(code, script, "exec"), g)
            log("预览地图创建完成")
        except Exception as e:
            import traceback
            traceback.print_exc()
            log_warn(f"预览地图创建失败: {e}")
    finally:
        _state["overview_executing"] = False
        _state["overview_cooldown"] = OVERVIEW_COOLDOWN_SECONDS


# ─────────────────────────────────────────────
# Tick
# ─────────────────────────────────────────────

def _tick_callback(delta_seconds):
    mat_running = _state.get("running")
    ov_running = _state.get("overview_running")
    if not (mat_running or ov_running):
        return False

    # ────────── 材质监听 ──────────
    if mat_running:
        cd = _state.get("cooldown", 0.0)
        if cd > 0:
            _state["cooldown"] = cd - delta_seconds
        elif not _state.get("executing"):
            if _state.get("last_count") is None:
                _state["last_count"] = _count_assets()
                _state["subdir_snapshot"] = _snapshot_subdirs()  # 建立子目录基线
                log(f"初始资产数: {_state['last_count']}  {_count_per_path()}")
            else:
                current = _count_assets()
                if current != _state["last_count"]:
                    _detect_new_skin_dirs()  # 顶层总数变了才做一次子目录对比
                    diff = current - _state["last_count"]
                    log(f"资产数变化: {_state['last_count']} → {current} ({diff:+d})  {_count_per_path()}")
                    _state["last_count"] = current
                    _state["pending"] = True
                    _state["countdown"] = DEBOUNCE_SECONDS

                if _state.get("pending"):
                    _state["countdown"] -= delta_seconds
                    if _state["countdown"] <= 0:
                        _state["pending"] = False
                        if AUTO_RUN:
                            _run_material_script()
                        else:
                            log("（AUTO_RUN=False，不执行）")

    # ────────── 预览地图监听 ──────────
    if ov_running:
        ov_cd = _state.get("overview_cooldown", 0.0)
        if ov_cd > 0:
            _state["overview_cooldown"] = ov_cd - delta_seconds
        elif _state.get("overview_pending") and not _state.get("overview_executing"):
            _state["overview_countdown"] -= delta_seconds
            if _state["overview_countdown"] <= 0:
                _state["overview_pending"] = False
                _run_overview_script()

    return True


# ─────────────────────────────────────────────
# 材质监听 — 对外接口
# ─────────────────────────────────────────────

def start():
    if _state.get("running"):
        log("材质监听已在运行")
        return True
    _ensure_script_path()
    _state["running"] = True
    _state["last_count"] = None
    _state["pending"] = False
    _state["countdown"] = 0.0
    _state["executing"] = False
    _state["cooldown"] = 0.0
    # ★ 重置皮肤检测状态：切换 Blender 工程后，旧会话的 last_changed_skin
    #   不能带进新一轮监听（否则可能指向旧工程的皮肤）
    _state["subdir_snapshot"] = {}
    _state["last_changed_skin"] = None
    if not _ensure_tick_registered():
        _state["running"] = False
        return False
    log("=" * 50)
    log("材质监听已启动")
    log(f"  监听目录: {', '.join(WATCH_PATHS)}")
    log(f"  防抖时间: {DEBOUNCE_SECONDS}s")
    log(f"  冷却时间: {COOLDOWN_SECONDS}s")
    if _state.get("material_script"):
        log(f"  材质脚本: {_state['material_script']}")
    else:
        log_warn("  材质脚本: 未找到")
    log("=" * 50)
    return True


def stop():
    if not _state.get("running"):
        log("材质监听未在运行")
        return False
    _state["running"] = False
    _state["pending"] = False
    _maybe_unregister_tick()
    log("=" * 50)
    log("材质监听已暂停")
    log("=" * 50)
    return True


def toggle():
    if _state.get("running"):
        return stop()
    return start()


def is_running():
    return _state.get("running", False)


# ─────────────────────────────────────────────
# 预览地图 — 对外接口
# ─────────────────────────────────────────────

def start_overview():
    if _state.get("overview_running"):
        log("自动预览地图已在运行")
        return True
    _ensure_overview_script_path()
    _state["overview_running"] = True
    _state["overview_pending"] = False
    _state["overview_countdown"] = 0.0
    _state["overview_executing"] = False
    _state["overview_cooldown"] = 0.0
    if not _ensure_tick_registered():
        _state["overview_running"] = False
        return False
    log("=" * 50)
    log("自动预览地图已启动")
    log(f"  触发条件: 材质处理完成")
    log(f"  防抖时间: {OVERVIEW_DEBOUNCE_SECONDS}s")
    log(f"  冷却时间: {OVERVIEW_COOLDOWN_SECONDS}s")
    if _state.get("overview_script"):
        log(f"  预览脚本: {_state['overview_script']}")
    else:
        log_warn(f"  预览脚本: 未找到 {OVERVIEW_SCRIPT_NAME}")
    if not _state.get("running"):
        log_warn("  提示: 材质监听未开启，暂无触发源（请同时开启自动处理材质）")
    log("=" * 50)
    return True


def stop_overview():
    if not _state.get("overview_running"):
        log("自动预览地图未在运行")
        return False
    _state["overview_running"] = False
    _state["overview_pending"] = False
    _maybe_unregister_tick()
    log("=" * 50)
    log("自动预览地图已暂停")
    log("=" * 50)
    return True


def toggle_overview():
    if _state.get("overview_running"):
        return stop_overview()
    return start_overview()


def is_overview_running():
    return _state.get("overview_running", False)


# ─────────────────────────────────────────────
# 手动一次性执行
# ─────────────────────────────────────────────

def run_once():
    """手动执行一次材质脚本（不启动监听、不触发预览地图）

    ★ 解耦决策：手动材质与预览地图完全独立，跑完材质不联动 overview 倒计时；
      如需刷新预览地图，单独点「手动预览地图-处理」(import create_overview as c; c.run())。
    """
    log("手动执行材质脚本")
    _ensure_script_path()
    if not _state.get("material_script"):
        log_warn(f"未找到 {TARGET_SCRIPT_NAME}")
        return False
    _run_material_script(trigger_overview=False)   # ★ 手动：不联动
    return True


def run_overview_once():
    """手动执行一次预览地图（不启动监听）"""
    log("手动创建预览地图")
    _ensure_overview_script_path()
    if not _state.get("overview_script"):
        log_warn(f"未找到 {OVERVIEW_SCRIPT_NAME}")
        return False
    _run_overview_script()
    return True


# ─────────────────────────────────────────────
# 抑制接口
# ─────────────────────────────────────────────

def suppress(seconds=120.0):
    """临时抑制材质自动触发（供外部脚本调用）"""
    _state["cooldown"] = max(_state.get("cooldown", 0.0), float(seconds))
    _state["pending"] = False
    _state["countdown"] = 0.0
    log(f"材质自动触发已抑制 {seconds:.0f} 秒")
    return True


def suppress_overview(seconds=120.0):
    """临时抑制预览地图自动触发"""
    _state["overview_cooldown"] = max(_state.get("overview_cooldown", 0.0), float(seconds))
    _state["overview_pending"] = False
    _state["overview_countdown"] = 0.0
    log(f"预览地图自动触发已抑制 {seconds:.0f} 秒")
    return True


# ─────────────────────────────────────────────
# 状态
# ─────────────────────────────────────────────

def status():
    log("")
    log("=" * 50)
    log(f"材质监听: {'运行中' if _state.get('running') else '已暂停'}")
    if _state.get("running"):
        log(f"  上次资产数: {_state.get('last_count')}")
        log(f"  正在执行: {_state.get('executing')}")
        cd = _state.get("cooldown", 0.0)
        if cd > 0:
            log(f"  冷却剩余: {cd:.1f}s")
    log(f"  材质脚本: {_state.get('material_script')}")

    log(f"自动预览地图: {'运行中' if _state.get('overview_running') else '已暂停'}")
    if _state.get("overview_running"):
        log(f"  等待触发: {_state.get('overview_pending')}")
        if _state.get("overview_pending"):
            log(f"  倒计时: {_state.get('overview_countdown', 0.0):.1f}s")
        ov_cd = _state.get("overview_cooldown", 0.0)
        if ov_cd > 0:
            log(f"  冷却剩余: {ov_cd:.1f}s")
    log(f"  预览脚本: {_state.get('overview_script')}")
    log("=" * 50)

    return _state.get("running", False)