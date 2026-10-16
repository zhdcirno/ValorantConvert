bl_info = {
    "name": "无畏契约模型转换",
    "author": "",
    "version": (1, 6, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > 瓦",
    "description": "Valorant模型转换工具",
    "category": "Import-Export",
}

import bpy
import addon_utils
import os
import re
import math
import json
import shutil
import zipfile
import tempfile
import urllib.request
from collections import Counter
from mathutils import Vector

# ═════════════════════════════════════════════
#  常量
# ═════════════════════════════════════════════

PSK_ADDON_MODULE = "io_scene_psk_psa"
PSK_EXTENSION_ID = "io_scene_psk_psa"
PSK_EXTENSION_URL = "https://extensions.blender.org/add-ons/io-scene-psk-psa/"

PSK_EXT_MODULE_NAMES = [
    "bl_ext.blender_org.io_scene_psk_psa",
    "bl_ext.user_default.io_scene_psk_psa",
    "bl_ext.system.io_scene_psk_psa",
    "io_scene_psk_psa",
]

SEND2UE_ADDON_MODULE = "send2ue"
SEND2UE_GITHUB_API = "https://api.github.com/repos/poly-hammer/BlenderTools/releases"
SEND2UE_RELEASES_URL = "https://github.com/poly-hammer/BlenderTools/releases"

LABEL_PSK_UNINSTALLED = "PSK插件安装"
LABEL_PSK_INSTALLED = "PSK插件安装(已安装)"
LABEL_SEND_UNINSTALLED = "Send插件安装"
LABEL_SEND_INSTALLED = "Send插件安装(已安装)"

GITHUB_PROXIES = [
    "",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://mirror.ghproxy.com/",
]

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/octet-stream, application/zip, */*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}

_PSK_IMPORT_OP_CANDIDATES = [
    "psk.import_file", "psk.import", "psa.import_file", "psa.import",
    "psa.import_all", "import_scene.psk", "import_scene.psa",
    "import_scene.psk_psa", "import_mesh.psk",
]

_GUN_TYPES = ('BasePistol', 'AutoShotgun', 'Melee', 'Burst', 'Carbine')
_ACCESSORY_KEYWORDS = ('RedDot', 'Silencer')

_TEX_SUFFIXES = ('DF', 'NM', 'MRS', 'AEM')
_BUILTIN_IMAGE_NAMES = ('Render Result', 'Viewer Node', 'Dice', 'Blender_Logo')
_SKIP_MAT_KEYWORDS = ('emissive', 'decals', 'decal')

_FIX_EXTRA_KEYWORDS = ('emissive', 'decal', 'radial', 'silencer', 'reddot')
_FIX_LOW_THRESHOLD = 0.30
_FIX_HIGH_THRESHOLD = 0.60


def log(msg):
    print(f"[无畏契约模型转换] {msg}")


# ═════════════════════════════════════════════
#  通用工具
# ═════════════════════════════════════════════

def refresh_addon_list():
    try:
        addon_utils.modules_refresh()
    except Exception as e:
        log(f"刷新插件列表失败: {e}")


def module_matches(name, target):
    if not name:
        return False
    return name == target or name.endswith("." + target)


def _is_addon_installed(target_module):
    try:
        for name in bpy.context.preferences.addons.keys():
            if module_matches(name, target_module):
                return True
    except Exception:
        pass
    try:
        is_enabled, _ = addon_utils.check(target_module)
        if is_enabled:
            return True
    except Exception:
        pass
    try:
        for mod in addon_utils.modules():
            name = mod.__name__
            if module_matches(name, target_module):
                try:
                    is_enabled, _ = addon_utils.check(name)
                    if is_enabled:
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def _get_addon_info(target_module):
    try:
        for mod in addon_utils.modules():
            if module_matches(mod.__name__, target_module):
                info = getattr(mod, "bl_info", {}) or {}
                version = info.get("version", (0, 0, 0))
                return {
                    "name": info.get("name", target_module),
                    "version": version,
                    "version_str": ".".join(str(v) for v in version),
                    "module": mod.__name__,
                }
    except Exception:
        pass
    return None


def ensure_online_access():
    if bpy.app.online_access_override:
        return False, "Blender 以 --offline-mode 启动，无法开启在线访问，请重启 Blender"
    if bpy.app.online_access:
        return True, "在线访问已开启"
    try:
        bpy.context.preferences.system.use_online_access = True
        try:
            bpy.ops.wm.save_userpref()
        except Exception:
            pass
    except Exception as e:
        return False, f"无法开启在线访问: {e}"
    if bpy.app.online_access:
        return True, "已自动开启在线访问权限"
    return False, "在线访问开启失败，请手动在 编辑→偏好设置→系统→网络 中勾选"


def _blender_version_tuple():
    v = bpy.app.version
    return (v[0], v[1])


def _is_repo_sync_broken():
    major, minor = _blender_version_tuple()
    return major == 5 and minor == 0


def sync_extension_repos():
    if _is_repo_sync_broken():
        log("跳过 repo_sync（Blender 5.0.x 该功能有已知 bug）")
        return False
    prefs = bpy.context.preferences
    if not hasattr(prefs, "extensions"):
        return False
    synced_any = False
    for i, repo in enumerate(prefs.extensions.repos):
        if not repo.enabled:
            continue
        try:
            bpy.ops.extensions.repo_sync(repo_index=i)
            synced_any = True
        except Exception as e:
            log(f"同步仓库 {repo.name} 失败（忽略）: {e}")
    if synced_any:
        refresh_addon_list()
    return synced_any


# ═════════════════════════════════════════════
#  网络请求
# ═════════════════════════════════════════════

def _http_get_json(url, timeout=30, max_retries=3):
    headers = dict(_BROWSER_HEADERS)
    headers["Accept"] = "application/vnd.github.v3+json"
    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_error = e
            log(f"API 请求失败（第{attempt+1}/{max_retries}次）: {e}")
    raise Exception(f"GitHub API 请求失败: {last_error}")


def _http_download_file(url, dst_path, max_retries=2):
    last_error = None
    for proxy in GITHUB_PROXIES:
        full_url = (proxy + url) if proxy else url
        for attempt in range(max_retries):
            try:
                log(f"下载中（{proxy or '直连'} 第{attempt+1}/{max_retries}次）")
                req = urllib.request.Request(full_url, headers=_BROWSER_HEADERS)
                with urllib.request.urlopen(req, timeout=90) as resp:
                    with open(dst_path, "wb") as f:
                        shutil.copyfileobj(resp, f)
                size = os.path.getsize(dst_path)
                if size < 1024:
                    raise Exception(f"下载文件异常小: {size} 字节")
                log(f"下载成功（{proxy or '直连'}，{size} 字节）")
                return True, f"下载成功（{proxy or '直连'}）"
            except Exception as e:
                last_error = e
                log(f"  失败: {e}")
    return False, f"所有下载源均失败：{last_error}"


# ═════════════════════════════════════════════
#  插件 zip 处理
# ═════════════════════════════════════════════

def _find_plugin_root(extract_dir, target_module_name):
    for root, dirs, files in os.walk(extract_dir):
        if os.path.basename(root) == target_module_name and "__init__.py" in files:
            return root
    candidates = []
    for root, dirs, files in os.walk(extract_dir):
        if "__init__.py" not in files:
            continue
        try:
            with open(os.path.join(root, "__init__.py"), "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(16384)
            low = content.lower()
            keywords = [target_module_name.lower(), "send to unreal", "psk", "psa"]
            if "bl_info" in low and any(k in low for k in keywords):
                depth = root[len(extract_dir):].count(os.sep)
                candidates.append((depth, root))
        except Exception:
            continue
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
    for root, dirs, files in os.walk(extract_dir):
        if "__init__.py" in files:
            return root
    return None


def _repack_plugin(plugin_root, out_zip_path, target_module_name):
    count = 0
    with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(plugin_root):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for file in files:
                full = os.path.join(root, file)
                rel = os.path.relpath(full, plugin_root)
                arcname = os.path.join(target_module_name, rel).replace("\\", "/")
                zf.write(full, arcname)
                count += 1
    log(f"重打包完成：{count} 个文件 → {os.path.basename(out_zip_path)}")


def _install_addon_from_downloaded_zip(downloaded_zip, module_name, temp_dir):
    try:
        extract_dir = os.path.join(temp_dir, "extract")
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(downloaded_zip, "r") as zf:
            zf.extractall(extract_dir)
        plugin_root = _find_plugin_root(extract_dir, module_name)
        if not plugin_root:
            return False, f"在 zip 中未找到插件根目录（模块名: {module_name}）"
        log(f"找到插件根目录: {plugin_root}")
        repacked_zip = os.path.join(temp_dir, f"{module_name}_repacked.zip")
        _repack_plugin(plugin_root, repacked_zip, module_name)
        try:
            bpy.ops.preferences.addon_install(filepath=repacked_zip, overwrite=True)
        except Exception as e:
            return False, f"安装失败: {e}"
        refresh_addon_list()
        for mod_name in (module_name, os.path.basename(plugin_root)):
            try:
                bpy.ops.preferences.addon_enable(module=mod_name)
                log(f"已启用模块: {mod_name}")
                break
            except Exception as e:
                log(f"启用 {mod_name} 失败: {e}")
        return True, "安装完成"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"安装失败: {e}"


# ═════════════════════════════════════════════
#  PSK 插件
# ═════════════════════════════════════════════

def is_psk_addon_installed():
    return _is_addon_installed(PSK_ADDON_MODULE)


def get_psk_addon_info():
    return _get_addon_info(PSK_ADDON_MODULE)


def install_psk_via_extension_platform():
    prefs = bpy.context.preferences
    if not hasattr(prefs, "extensions"):
        return False, "当前 Blender 版本不支持扩展平台"
    if not _is_repo_sync_broken():
        try:
            sync_extension_repos()
        except Exception as e:
            log(f"仓库同步异常（忽略）: {e}")
    for i, repo in enumerate(prefs.extensions.repos):
        if not repo.enabled:
            continue
        try:
            log(f"尝试从仓库 '{repo.name}' 安装 PSK (pkg_id={PSK_EXTENSION_ID})")
            result = bpy.ops.extensions.package_install(repo_index=i, pkg_id=PSK_EXTENSION_ID)
            if "FINISHED" in result:
                refresh_addon_list()
                if is_psk_addon_installed():
                    log(f"仓库 '{repo.name}' 安装 PSK 成功")
                    return True, "扩展安装成功"
                log(f"仓库 '{repo.name}' 返回 FINISHED 但 PSK 未真正安装")
        except Exception as e:
            log(f"从仓库 {repo.name} 安装 PSK 失败: {e}")
            continue
    return False, "扩展平台未找到 PSK 插件"


# 离线兜底提示：网络下载失败时引导用户使用仓库内置 / 官方离线包手动安装
_MANUAL_INSTALL_HINT_PSK = (
    "【离线兜底】网络下载失败。可手动安装仓库内置包："
    "blender/vendor/io_scene_psk_psa_v9.1.3.zip"
    "（Blender 5.x → Edit → Preferences → Extensions → Install from Disk → 选该 zip）。"
    "详细说明见 blender/vendor/README.md。"
)
_MANUAL_INSTALL_HINT_SEND2UE = (
    "【离线兜底】网络下载失败。Send to Unreal 不随仓库分发，请从官方 Release 手动下载："
    "https://github.com/poly-hammer/BlenderTools/releases （取 send2ue_2.6.7.zip），"
    "Blender → Edit → Preferences → Add-ons → Install from Disk → 选 zip。详见 blender/vendor/README.md。"
)


def install_psk_from_github():
    temp_dir = None
    try:
        api_url = "https://api.github.com/repos/DarklightGames/io_scene_psk_psa/releases/latest"
        try:
            release_data = _http_get_json(api_url, timeout=30)
        except Exception as e:
            return False, f"获取 PSK release 失败: {e}"
        zip_url = None
        for asset in release_data.get("assets", []):
            if asset["name"].endswith(".zip"):
                zip_url = asset["browser_download_url"]
                break
        if not zip_url:
            zip_url = release_data.get("zipball_url")
        if not zip_url:
            return False, "未找到可下载的文件"
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, "psk_raw.zip")
        ok, msg = _http_download_file(zip_url, zip_path)
        if not ok:
            return False, msg + "\n" + _MANUAL_INSTALL_HINT_PSK
        success, message = _install_addon_from_downloaded_zip(zip_path, PSK_ADDON_MODULE, temp_dir)
        if not success:
            return False, message
        if is_psk_addon_installed():
            return True, "PSK插件安装成功（GitHub）"
        return False, "PSK 安装后未能启用"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"下载失败: {e}"
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def install_psk_addon():
    success, msg = install_psk_via_extension_platform()
    if success and is_psk_addon_installed():
        return True, msg
    log(f"PSK扩展平台安装失败({msg})，回退 GitHub...")
    return install_psk_from_github()


# ═════════════════════════════════════════════
#  Send to Unreal
# ═════════════════════════════════════════════

def is_send2ue_installed():
    return _is_addon_installed(SEND2UE_ADDON_MODULE)


def get_send2ue_info():
    return _get_addon_info(SEND2UE_ADDON_MODULE)


def _parse_version(text):
    if not text:
        return (0, 0, 0)
    m = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text)
    if m:
        g = m.groups()
        return (int(g[0]), int(g[1]), int(g[2]) if g[2] else 0)
    return (0, 0, 0)


def _fetch_latest_send2ue_release():
    api_url = f"{SEND2UE_GITHUB_API}?per_page=50"
    releases = _http_get_json(api_url, timeout=30)
    candidates = []
    for release in releases:
        tag = release.get("tag_name", "")
        for asset in release.get("assets", []):
            name = asset["name"]
            lower = name.lower()
            if lower.startswith("send2ue") and lower.endswith(".zip"):
                ver = _parse_version(name)
                if ver == (0, 0, 0):
                    ver = _parse_version(tag)
                candidates.append({
                    "url": asset["browser_download_url"],
                    "tag": tag, "name": name, "version": ver,
                })
    if candidates:
        candidates.sort(key=lambda c: c["version"], reverse=True)
        best = candidates[0]
        log(f"选中 send2ue 资产: {best['name']} (tag={best['tag']}, ver={best['version']})")
        return best["url"], best["tag"], "asset"
    for release in releases:
        for asset in release.get("assets", []):
            if asset["name"].endswith(".zip"):
                return asset["browser_download_url"], release.get("tag_name", ""), "asset"
    if releases:
        return releases[0].get("zipball_url"), releases[0].get("tag_name", ""), "source"
    return None, None, None


def install_send2ue_from_github():
    temp_dir = None
    try:
        try:
            zip_url, tag, kind = _fetch_latest_send2ue_release()
        except Exception as e:
            return False, f"获取 Send release 失败: {e}"
        if not zip_url:
            return False, "未找到可下载的 Send to Unreal 文件"
        log(f"下载源类型: {kind}，版本: {tag}")
        temp_dir = tempfile.mkdtemp()
        downloaded_zip = os.path.join(temp_dir, "download.zip")
        ok, msg = _http_download_file(zip_url, downloaded_zip)
        if not ok:
            return False, msg + "\n" + _MANUAL_INSTALL_HINT_SEND2UE
        success, message = _install_addon_from_downloaded_zip(downloaded_zip, SEND2UE_ADDON_MODULE, temp_dir)
        if not success:
            return False, message
        if is_send2ue_installed():
            return True, f"Send插件安装成功（{tag or 'poly-hammer'}）"
        return False, (
            f"Send插件已安装但启用失败（{tag or 'poly-hammer'}）。"
            "该版本可能与当前 Blender 不兼容，请重启 Blender 后手动启用。"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"安装失败: {e}"
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def install_send2ue_addon():
    return install_send2ue_from_github()


def open_send2ue_github():
    import webbrowser
    webbrowser.open(SEND2UE_RELEASES_URL)


# ═════════════════════════════════════════════
#  PSK 导入操作符探测
# ═════════════════════════════════════════════

def _list_registered_ops(submod_name):
    try:
        submod = getattr(bpy.ops, submod_name, None)
        if submod is None:
            return []
        return [n for n in dir(submod) if not n.startswith("_")]
    except Exception:
        return []


def _is_op_registered(full_name):
    try:
        parts = full_name.split(".", 1)
        if len(parts) != 2:
            return False
        mod_name, op_name = parts
        return op_name in _list_registered_ops(mod_name)
    except Exception:
        return False


def find_psk_import_operator():
    for full_name in _PSK_IMPORT_OP_CANDIDATES:
        if _is_op_registered(full_name):
            return full_name
    for sub in ("psk", "psa"):
        op_names = _list_registered_ops(sub)
        for name in op_names:
            low = name.lower()
            if "import" in low and ("file" in low or "psk" in low or "psa" in low):
                return f"{sub}.{name}"
    for sub in ("import_scene", "import_mesh"):
        op_names = _list_registered_ops(sub)
        for name in op_names:
            low = name.lower()
            if "psk" in low or "psa" in low:
                return f"{sub}.{name}"
    return None


def call_psk_import(op_full_name, filepath):
    mod_name, op_name = op_full_name.split(".", 1)
    fn = getattr(getattr(bpy.ops, mod_name), op_name)
    return fn(filepath=filepath)


def force_reenable_psk_addon():
    enabled_name = None
    try:
        for name in list(bpy.context.preferences.addons.keys()):
            if module_matches(name, PSK_ADDON_MODULE):
                enabled_name = name
                break
    except Exception:
        pass
    if enabled_name:
        try:
            log(f"尝试禁用再重新启用 {enabled_name}")
            bpy.ops.preferences.addon_disable(module=enabled_name)
            bpy.ops.preferences.addon_enable(module=enabled_name)
            refresh_addon_list()
            return True
        except Exception as e:
            log(f"重新启用失败: {e}")
    for mod_name in PSK_EXT_MODULE_NAMES:
        try:
            bpy.ops.preferences.addon_enable(module=mod_name)
            refresh_addon_list()
            log(f"已启用 {mod_name}")
            return True
        except Exception as e:
            log(f"启用 {mod_name} 失败: {e}")
    return False


# ═════════════════════════════════════════════
#  一键导入
# ═════════════════════════════════════════════

_MESH_KEYWORDS = ('skelmesh', 'staticmesh', 'skeleton', 'static', 'mesh', 'skel')
_LEVEL_PATTERN = re.compile(r'^Lv\d+$', re.IGNORECASE)


def find_psk_files(folder):
    result = []
    if not folder or not os.path.isdir(folder):
        return result
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(('.psk', '.pskx')):
                result.append(os.path.join(root, f))
    return sorted(result)


def detect_skin_name_from_file(filepath):
    basename = os.path.splitext(os.path.basename(filepath))[0]
    parts = basename.split('_')
    if parts and parts[-1].lower() in _MESH_KEYWORDS:
        parts.pop()
    if parts and _LEVEL_PATTERN.match(parts[-1]):
        parts.pop()
    if parts:
        return parts[-1]
    return ""


def detect_skin_name_from_folder(folder):
    files = find_psk_files(folder)
    if not files:
        return ""
    counter = Counter()
    for f in files:
        name = detect_skin_name_from_file(f)
        if name:
            counter[name] += 1
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def clear_current_scene():
    for obj in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
    export_coll = bpy.data.collections.get("Export")
    if export_coll is not None:
        sub_names = [sub.name for sub in list(export_coll.children)]
        for sub_name in sub_names:
            sub = bpy.data.collections.get(sub_name)
            if sub is None:
                continue
            try:
                for parent in list(bpy.data.collections):
                    if parent == sub:
                        continue
                    try:
                        if sub.name in [c.name for c in parent.children]:
                            parent.children.unlink(sub)
                    except Exception:
                        pass
                try:
                    if sub.name in [c.name for c in bpy.context.scene.collection.children]:
                        bpy.context.scene.collection.children.unlink(sub)
                except Exception:
                    pass
                bpy.data.collections.remove(sub)
                log(f"删除子集合: {sub_name}")
            except Exception as e:
                log(f"删除子集合 {sub_name} 失败: {e}")
    for coll in (
        bpy.data.meshes, bpy.data.armatures, bpy.data.materials,
        bpy.data.images, bpy.data.actions, bpy.data.curves,
        bpy.data.cameras, bpy.data.lights, bpy.data.lattices, bpy.data.fonts,
    ):
        for block in list(coll):
            if block.users == 0:
                try:
                    coll.remove(block)
                except Exception:
                    pass
    export_coll = bpy.data.collections.get("Export")
    for coll in list(bpy.data.collections):
        if coll == export_coll:
            continue
        try:
            if coll.users == 0 and coll.users_scene == 0:
                bpy.data.collections.remove(coll)
        except Exception:
            pass


# ═════════════════════════════════════════════
#  一键处理 - 针对模型处理配置
# ═════════════════════════════════════════════

_GUN_PROCESS_CONFIG = {
    'BasePistol': {
        'static_pos': (0.1683, 0.0, -2.5027),
        'static_rot': (0.0, 21.544, 0.0),
        'copies': [],
    },
    'AutoShotgun': {
        'static_pos': (26.252, 0.0, -1.9821),
        'static_rot': None,
        'copies': [],
    },
    'Melee': {
        'static_pos': None,
        'static_rot': None,
        'copies': [],
    },
    'Burst': {
        'static_pos': (-16.247, 0.0, -2.2674),
        'static_rot': None,
        'copies': [
            {'source': 'RedDot', 'pos': (8.0275, 0.000002, 14.574), 'rot': None},
        ],
    },
    'Carbine': {
        'static_pos': (20.85, 0.0, -1.717),
        'static_rot': None,
        'copies': [
            {'source': 'RedDot', 'pos': (20.986, 0.0, 15.022), 'rot': None},
            {'source': 'Silencer', 'pos': (55.447, 0.0, 9.8754), 'rot': None},
        ],
    },
}


def _is_static_mesh_name(name):
    low = name.lower()
    return 'staticmesh' in low and 'skelmesh' not in low


def _detect_gun_type_from_name(name):
    for gun in _GUN_TYPES:
        if gun.lower() in name.lower():
            return gun
    return None


def _select_tree(root_obj):
    try:
        root_obj.select_set(True)
    except Exception:
        pass
    for child in root_obj.children:
        _select_tree(child)


def _duplicate_tree(root_obj):
    bpy.ops.object.select_all(action='DESELECT')
    _select_tree(root_obj)
    try:
        bpy.ops.object.duplicate()
    except Exception as e:
        log(f"复制对象树失败: {e}")
        return None
    new_selected = list(bpy.context.selected_objects)
    if not new_selected:
        return None
    new_set = set(new_selected)
    for o in new_selected:
        if o.parent is None or o.parent not in new_set:
            return o
    return new_selected[0]


def _assign_to_collection(root_obj, target_coll):
    if root_obj is None or target_coll is None:
        return
    objects = [root_obj] + list(root_obj.children_recursive)
    for obj in objects:
        for c in list(obj.users_collection):
            if c == target_coll:
                continue
            try:
                c.objects.unlink(obj)
            except Exception:
                pass
        if target_coll not in obj.users_collection:
            try:
                target_coll.objects.link(obj)
            except Exception as e:
                log(f"加入 {target_coll.name} 失败: {obj.name} - {e}")


def _get_or_create_subcollection(parent_coll, name):
    sub = bpy.data.collections.get(name)
    if sub is None:
        sub = bpy.data.collections.new(name)
    if sub.name not in [c.name for c in parent_coll.children]:
        try:
            parent_coll.children.link(sub)
        except Exception as e:
            log(f"链接子集合 {name} 失败: {e}")
    return sub


def process_remove_static_modifiers():
    removed = 0
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if not _is_static_mesh_name(obj.name):
            continue
        for mod in list(obj.modifiers):
            try:
                obj.modifiers.remove(mod)
                removed += 1
            except Exception:
                pass
    return removed


def process_delete_accessories():
    to_delete = set()
    for obj in bpy.data.objects:
        name = obj.name
        if not _is_static_mesh_name(name):
            continue
        if any(kw.lower() in name.lower() for kw in _ACCESSORY_KEYWORDS):
            to_delete.add(obj)
    changed = True
    while changed:
        changed = False
        for obj in list(bpy.data.objects):
            if obj in to_delete:
                continue
            if obj.type not in ('EMPTY', 'ARMATURE'):
                continue
            children = list(obj.children)
            if not children:
                continue
            if all(c in to_delete for c in children):
                to_delete.add(obj)
                changed = True
    deleted_names = []
    for obj in to_delete:
        try:
            name = obj.name
            bpy.data.objects.remove(obj, do_unlink=True)
            deleted_names.append(name)
        except Exception:
            pass
    return deleted_names


def process_move_to_export_collection():
    scene = bpy.context.scene
    config = getattr(scene, "valorant_import_config", "gun") or "gun"

    export_coll = bpy.data.collections.get("Export")
    if export_coll is None:
        export_coll = bpy.data.collections.new("Export")
    try:
        if export_coll.name not in [c.name for c in scene.collection.children]:
            scene.collection.children.link(export_coll)
    except Exception:
        pass

    moved = 0

    if config == "charm":
        skin = (getattr(scene, "valorant_skin_name", "") or "").strip()
        sub_name = skin if skin else "Charm"
        charm_coll = _get_or_create_subcollection(export_coll, sub_name)
        for obj in bpy.data.objects:
            if obj.type not in ('MESH', 'ARMATURE', 'EMPTY'):
                continue
            for c in list(obj.users_collection):
                if c == charm_coll:
                    continue
                try:
                    c.objects.unlink(obj)
                except Exception:
                    pass
            if charm_coll not in obj.users_collection:
                try:
                    charm_coll.objects.link(obj)
                    moved += 1
                except Exception:
                    pass
        return moved

    gun_types_in_scene = set()
    for obj in bpy.data.objects:
        if obj.type not in ('MESH', 'ARMATURE', 'EMPTY'):
            continue
        gun = _detect_gun_type_from_name(obj.name)
        if gun:
            gun_types_in_scene.add(gun)

    sub_collections = {}
    for gun in sorted(gun_types_in_scene):
        sub_collections[gun] = _get_or_create_subcollection(export_coll, gun)

    keep_set = set(sub_collections.values())
    keep_set.add(export_coll)

    for obj in bpy.data.objects:
        if obj.type not in ('MESH', 'ARMATURE', 'EMPTY'):
            continue
        gun = _detect_gun_type_from_name(obj.name)
        target_coll = sub_collections.get(gun) if gun else None
        for coll in list(obj.users_collection):
            if coll in keep_set:
                continue
            try:
                coll.objects.unlink(obj)
            except Exception:
                pass
        if target_coll is not None:
            if target_coll not in obj.users_collection:
                try:
                    target_coll.objects.link(obj)
                    moved += 1
                except Exception:
                    pass
            if export_coll in obj.users_collection:
                try:
                    export_coll.objects.unlink(obj)
                except Exception:
                    pass
        else:
            if export_coll not in obj.users_collection:
                try:
                    export_coll.objects.link(obj)
                    moved += 1
                except Exception:
                    pass
    return moved


def _find_move_target(mesh_obj):
    parent = mesh_obj.parent
    while parent:
        if parent.type == 'EMPTY':
            return parent
        parent = parent.parent
    parent = mesh_obj.parent
    while parent:
        if parent.type == 'ARMATURE':
            return parent
        parent = parent.parent
    return mesh_obj


def _find_gun_static_meshes(gun_type):
    results = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if not _is_static_mesh_name(obj.name):
            continue
        if gun_type.lower() in obj.name.lower():
            results.append(obj)
    return results


def _find_accessory_roots(keyword):
    kw_lower = keyword.lower()
    matched = [o for o in bpy.data.objects if kw_lower in o.name.lower()]
    if not matched:
        return []
    matched_set = set(matched)
    roots = []
    for o in matched:
        if o.parent is None or o.parent not in matched_set:
            roots.append(o)
    return roots


def _apply_transform(obj, pos, rot):
    if pos is not None:
        obj.location = Vector(pos)
    if rot is not None:
        obj.rotation_euler = (
            math.radians(rot[0]),
            math.radians(rot[1]),
            math.radians(rot[2]),
        )


def process_gun_specific():
    results = []
    copied_names = set()

    scene = bpy.context.scene
    config = getattr(scene, "valorant_import_config", "gun") or "gun"
    export_coll = bpy.data.collections.get("Export")

    for gun_type, config_data in _GUN_PROCESS_CONFIG.items():
        static_pos = config_data.get('static_pos')
        static_rot = config_data.get('static_rot')
        copies = config_data.get('copies', [])

        if static_pos is None and not copies and static_rot is None:
            continue

        if static_pos is not None or static_rot is not None:
            static_meshes = _find_gun_static_meshes(gun_type)
            moved = set()
            for mesh_obj in static_meshes:
                target = _find_move_target(mesh_obj)
                if target in moved:
                    continue
                moved.add(target)
                try:
                    _apply_transform(target, static_pos, static_rot)
                    results.append(f"{gun_type}: 移动 {target.name}")
                except Exception as e:
                    log(f"移动失败: {e}")

        for spec in copies:
            source_kw = spec['source']
            copy_pos = spec.get('pos')
            copy_rot = spec.get('rot')
            roots = _find_accessory_roots(source_kw)
            if not roots:
                results.append(f"{gun_type}: 未找到 {source_kw}")
                continue
            root = roots[0]
            new_root = _duplicate_tree(root)
            if new_root:
                try:
                    _apply_transform(new_root, copy_pos, copy_rot)

                    # ★ 命名规则：GN_{枪械类型}_{配件名}_Skelmesh
                    #   让 UE 端能识别归属
                    new_empty_name = f"GN_{gun_type}_{source_kw}"
                    new_mesh_name = f"GN_{gun_type}_{source_kw}_Skelmesh"

                    if new_root.type == 'EMPTY':
                        new_root.name = new_empty_name
                    else:
                        new_root.name = new_mesh_name
                        if new_root.data:
                            new_root.data.name = new_mesh_name

                    copied_names.add(new_root.name)

                    # 改所有子 Mesh 的名字
                    for c in new_root.children_recursive:
                        if c.type == 'MESH':
                            c.name = new_mesh_name
                            if c.data:
                                c.data.name = new_mesh_name
                        copied_names.add(c.name)

                    if export_coll is not None:
                        if config == "charm":
                            skin = (getattr(scene, "valorant_skin_name", "") or "").strip()
                            sub_name = skin if skin else "Charm"
                            target_coll = _get_or_create_subcollection(export_coll, sub_name)
                        else:
                            target_coll = _get_or_create_subcollection(export_coll, gun_type)
                        _assign_to_collection(new_root, target_coll)

                    results.append(f"{gun_type}: 复制 {source_kw}")
                except Exception as e:
                    log(f"移动副本失败: {e}")

    deleted = _delete_all_accessories(copied_names)
    results.append(f"删除原始配件: {deleted} 个")
    return results


def _delete_all_accessories(keep_names=None):
    if keep_names is None:
        keep_names = set()
    targets = []
    for obj in bpy.data.objects:
        name = obj.name
        if name in keep_names:
            continue
        if "_copy" in name:
            continue
        if any(kw.lower() in name.lower() for kw in _ACCESSORY_KEYWORDS):
            targets.append(obj)
    targets.sort(key=lambda o: len(o.children))
    deleted = 0
    for obj in targets:
        try:
            _ = obj.name
        except ReferenceError:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            deleted += 1
        except Exception:
            pass
    return deleted


# ═════════════════════════════════════════════
#  材质处理
# ═════════════════════════════════════════════

def _scan_textures_in_folder(root_folder):
    result = {}
    if not root_folder or not os.path.isdir(root_folder):
        return result
    for r, dirs, files in os.walk(root_folder):
        for f in files:
            if not f.lower().endswith(('.png', '.tga', '.tif', '.jpg', '.jpeg')):
                continue
            base, _ = os.path.splitext(f)
            if (base.endswith('_Metallic') or base.endswith('_Roughness')
                    or base.endswith('_Specular')):
                continue
            for suffix in _TEX_SUFFIXES:
                if base.endswith('_' + suffix):
                    mat_base = base[:-(len(suffix) + 1)]
                    if mat_base.endswith('_3p'):
                        continue
                    result.setdefault(mat_base, {})[suffix] = os.path.join(r, f)
                    break
    return result


def find_texture_sets(model_folder):
    if not model_folder or not os.path.isdir(model_folder):
        return {}, ""

    parent = os.path.dirname(model_folder.rstrip("/\\"))

    if parent:
        materials_dir = os.path.join(parent, "Materials")
        if os.path.isdir(materials_dir):
            result = _scan_textures_in_folder(materials_dir)
            if result:
                log(f"从同级 Materials 找到 {len(result)} 套贴图: {materials_dir}")
                return result, materials_dir

    result = _scan_textures_in_folder(model_folder)
    if result:
        log(f"从模型目录本身找到 {len(result)} 套贴图")
        return result, model_folder

    if parent:
        result = _scan_textures_in_folder(parent)
        if result:
            log(f"从上级目录递归找到 {len(result)} 套贴图: {parent}")
            return result, parent

    return {}, ""


def should_skip_material(mat_name):
    low = mat_name.lower()
    return any(kw in low for kw in _SKIP_MAT_KEYWORDS)


def match_material_to_texture_set(mat_name, texture_map):
    if mat_name.endswith('_MI'):
        base = mat_name[:-3]
        if base in texture_map:
            return base
    best_match, best_len = None, 0
    for base_name in texture_map.keys():
        tokens = base_name.split('_')
        if all(t in mat_name for t in tokens):
            if len(tokens) > best_len:
                best_match = base_name
                best_len = len(tokens)
    return best_match


def load_image(path, colorspace):
    img = bpy.data.images.load(path, check_existing=True)
    try:
        img.colorspace_settings.name = colorspace
    except Exception:
        pass
    return img


def _get_bsdf_input(bsdf, names):
    for name in names:
        if name in bsdf.inputs:
            return bsdf.inputs[name]
    return None


def rebuild_material_nodes(mat, tex_paths):
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (600, 0)
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (300, 0)
    links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

    if 'DF' in tex_paths:
        tex = nodes.new('ShaderNodeTexImage')
        tex.location = (-400, 500)
        tex.image = load_image(tex_paths['DF'], 'sRGB')
        bc_in = _get_bsdf_input(bsdf, ['Base Color'])
        if bc_in:
            links.new(tex.outputs['Color'], bc_in)

    if 'NM' in tex_paths:
        tex = nodes.new('ShaderNodeTexImage')
        tex.location = (-600, 200)
        tex.image = load_image(tex_paths['NM'], 'Non-Color')
        nm_node = nodes.new('ShaderNodeNormalMap')
        nm_node.location = (-200, 200)
        links.new(tex.outputs['Color'], nm_node.inputs['Color'])
        n_in = _get_bsdf_input(bsdf, ['Normal'])
        if n_in:
            links.new(nm_node.outputs['Normal'], n_in)

    if 'MRS' in tex_paths:
        tex = nodes.new('ShaderNodeTexImage')
        tex.location = (-400, -100)
        tex.image = load_image(tex_paths['MRS'], 'Non-Color')
        r_in = _get_bsdf_input(bsdf, ['Roughness'])
        if r_in:
            links.new(tex.outputs['Color'], r_in)

    if 'AEM' in tex_paths:
        tex = nodes.new('ShaderNodeTexImage')
        tex.location = (-400, -400)
        tex.image = load_image(tex_paths['AEM'], 'Non-Color')
        s_in = _get_bsdf_input(bsdf, ['Specular IOR Level', 'Specular'])
        if s_in:
            links.new(tex.outputs['Color'], s_in)


def process_create_materials():
    scene = bpy.context.scene
    model_folder = scene.valorant_import_folder or ""

    stats = {
        'processed': 0,
        'skipped_special': 0,
        'skipped_no_match': 0,
        'texture_folder': "",
        'texture_sets': 0,
    }
    details = []

    if not model_folder or not os.path.isdir(model_folder):
        return stats, [('错误', '', f'模型文件夹无效: {model_folder}')]

    texture_map, found_folder = find_texture_sets(model_folder)
    stats['texture_folder'] = found_folder
    stats['texture_sets'] = len(texture_map)

    if not texture_map:
        return stats, [('错误', '', f'未找到 DF/NM/MRS/AEM 贴图（从 {model_folder} 搜索）')]

    log(f"找到 {len(texture_map)} 套贴图: {sorted(texture_map.keys())}")

    processed = set()
    mesh_objs = [o for o in bpy.data.objects if o.type == 'MESH']

    for obj in mesh_objs:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            mat_name = mat.name
            if mat_name in processed:
                continue

            if should_skip_material(mat_name):
                stats['skipped_special'] += 1
                details.append(('跳过(特殊)', mat_name, ''))
                continue

            mat_base = match_material_to_texture_set(mat_name, texture_map)
            if not mat_base:
                stats['skipped_no_match'] += 1
                details.append(('跳过(无贴图)', mat_name, ''))
                continue

            try:
                rebuild_material_nodes(mat, texture_map[mat_base])
                processed.add(mat_name)
                stats['processed'] += 1
                details.append(('处理', mat_name, mat_base))
            except Exception as e:
                import traceback
                traceback.print_exc()
                details.append(('失败', mat_name, str(e)))

    return stats, details


# ─────────────────────────────────────────────
#  材质槽修复（按面数占比交换材质引用）
# ─────────────────────────────────────────────

def _is_main_appearance_mat(mat_name):
    low = mat_name.lower()
    return not any(kw in low for kw in _FIX_EXTRA_KEYWORDS)


def process_fix_material_slots():
    swapped = 0
    details = []
    mesh_objs = [o for o in bpy.data.objects if o.type == 'MESH']

    for obj in mesh_objs:
        me = obj.data
        total = len(me.polygons)
        if total == 0:
            continue

        slot_faces = {}
        for poly in me.polygons:
            idx = poly.material_index
            slot_faces[idx] = slot_faces.get(idx, 0) + 1

        main_idx = None
        for i, slot in enumerate(obj.material_slots):
            if slot.material and _is_main_appearance_mat(slot.material.name):
                main_idx = i
                break

        if main_idx is None or not slot_faces:
            continue

        top_idx = max(slot_faces.keys(), key=lambda i: slot_faces[i])
        if main_idx == top_idx:
            continue

        main_faces = slot_faces.get(main_idx, 0)
        top_faces = slot_faces[top_idx]

        main_pct = main_faces / total
        top_pct = top_faces / total

        if main_pct >= _FIX_LOW_THRESHOLD:
            continue
        if top_pct <= _FIX_HIGH_THRESHOLD:
            continue

        try:
            slots = obj.material_slots
            mat_a = slots[main_idx].material
            mat_b = slots[top_idx].material
            slots[main_idx].material = mat_b
            slots[top_idx].material = mat_a

            name_a = mat_a.name if mat_a else "(空)"
            name_b = mat_b.name if mat_b else "(空)"
            details.append(
                f"{obj.name}: [{main_idx}]{name_a} ↔ [{top_idx}]{name_b}"
            )
            swapped += 1
        except Exception as e:
            log(f"  交换失败 {obj.name}: {e}")

    return swapped, details


def process_all_in_one():
    results = []
    r1 = process_remove_static_modifiers()
    results.append(f"① 删除静态修改器: {r1} 个")
    r2 = process_delete_accessories()
    results.append(f"② 删除多余配件: {len(r2)} 个")
    r3 = process_move_to_export_collection()
    results.append(f"③ 放入 Export 集合: {r3} 个")
    r4 = process_gun_specific()
    results.append(f"④ 针对模型处理: {len(r4)} 条")
    stats, _ = process_create_materials()
    if stats['texture_folder']:
        results.append(
            f"⑤ 创建材质: 处理 {stats['processed']}，"
            f"跳过特殊 {stats['skipped_special']}，"
            f"跳过无贴图 {stats['skipped_no_match']}"
        )
    else:
        results.append(f"⑤ 创建材质: 未找到贴图")
    swapped, _ = process_fix_material_slots()
    results.append(f"⑥ 修复材质槽: 交换 {swapped} 个")
    return results


# ═════════════════════════════════════════════
#  一键导出
# ═════════════════════════════════════════════

def _collect_static_parents():
    parents = set()
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if not _is_static_mesh_name(obj.name):
            continue
        target = _find_move_target(obj)
        if target is not None:
            parents.add(target)
    return list(parents)


def _apply_scale_to_objects(objects, scale_factor):
    if not objects:
        return 0
    try:
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    try:
        bpy.ops.object.select_all(action='DESELECT')
    except Exception:
        pass
    valid = []
    for obj in objects:
        try:
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.select_set(True)
            valid.append(obj)
        except Exception:
            pass
    if not valid:
        return 0
    try:
        bpy.context.view_layer.objects.active = valid[0]
    except Exception:
        pass
    for obj in valid:
        try:
            obj.scale = (
                obj.scale[0] * scale_factor,
                obj.scale[1] * scale_factor,
                obj.scale[2] * scale_factor,
            )
        except Exception:
            pass
    try:
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    except Exception as e:
        log(f"应用变换失败: {e}")
        return 0
    return len(valid)


def process_static_scale_up():
    parents = _collect_static_parents()
    if not parents:
        return 0, "未找到静态网格体父级"
    count = _apply_scale_to_objects(parents, 100.0)
    return count, f"放大 {count} 个父级 ×100"


def process_static_scale_down():
    parents = _collect_static_parents()
    if not parents:
        return 0, "未找到静态网格体父级"
    count = _apply_scale_to_objects(parents, 0.01)
    return count, f"缩小 {count} 个父级 ×0.01"


def process_configure_send2ue():
    scene = bpy.context.scene
    skin = scene.valorant_skin_name or ""

    if not is_send2ue_installed():
        return False, "send2ue 插件未安装"

    if not hasattr(scene, "send2ue"):
        return False, "未找到 scene.send2ue"

    config = getattr(scene, "valorant_import_config", "gun") or "gun"
    if config == "charm":
        base_path = "/Game/Charm/"
    else:
        if not skin:
            return False, "枪皮名称为空"
        base_path = f"/Game/Gun/{skin}/"

    send2ue = scene.send2ue
    changes = []

    try:
        send2ue.unreal_mesh_folder_path = base_path
        changes.append(f"mesh={base_path}")
    except Exception as e:
        log(f"设置 unreal_mesh_folder_path 失败: {e}")

    try:
        send2ue.unreal_animation_folder_path = base_path + "animations/"
        changes.append(f"anim={base_path}animations/")
    except Exception as e:
        log(f"设置 unreal_animation_folder_path 失败: {e}")

    try:
        send2ue.unreal_groom_folder_path = base_path + "groom/"
        changes.append(f"groom={base_path}groom/")
    except Exception as e:
        log(f"设置 unreal_groom_folder_path 失败: {e}")

    try:
        ext = send2ue.extensions.use_collections_as_folders
        ext.use_collections_as_folders = True
        changes.append("use_collections_as_folders=True")
    except Exception as e:
        log(f"设置 use_collections_as_folders 失败: {e}")

    if not changes:
        return False, "未能设置任何属性"

    return True, " | ".join(changes)


def process_send_to_ue():
    if not is_send2ue_installed():
        return False, "send2ue 插件未安装"
    if not hasattr(bpy.ops.wm, "send2ue"):
        return False, "bpy.ops.wm.send2ue 未注册"
    try:
        result = bpy.ops.wm.send2ue()
        if "FINISHED" in result:
            return True, f"导出成功: {result}"
        return False, f"返回: {result}"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"调用失败: {e}"


def process_save_blend():
    scene = bpy.context.scene
    folder = scene.valorant_import_folder or ""
    skin = scene.valorant_skin_name or ""
    if not folder or not os.path.isdir(folder):
        return False, "导入文件夹无效"
    if not skin:
        return False, "枪皮名称为空"
    filepath = os.path.join(folder, f"{skin}.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=filepath, check_existing=False)
        return True, f"已保存到 {filepath}"
    except Exception as e:
        return False, f"保存失败: {e}"


def process_export_all():
    results = []
    ok, msg = process_configure_send2ue()
    results.append(f"① 配置 Send 插件: {'✓' if ok else '✗'} {msg}")
    if not ok:
        return results
    count, msg = process_static_scale_up()
    results.append(f"② 静态放大: {msg}")
    ok, msg = process_send_to_ue()
    results.append(f"③ 导出到 UE: {'✓' if ok else '✗'} {msg}")
    count, msg = process_static_scale_down()
    results.append(f"④ 静态缩小: {msg}")
    ok, msg = process_save_blend()
    results.append(f"⑤ 保存文件: {'✓' if ok else '✗'} {msg}")
    return results


# ═════════════════════════════════════════════
#  操作符
# ═════════════════════════════════════════════

class VALORANT_OT_RedetectSkinName(bpy.types.Operator):
    bl_idname = "valorant.redetect_skin_name"
    bl_label = "重新检测"

    def execute(self, context):
        folder = context.scene.valorant_import_folder
        if not folder:
            self.report({"ERROR"}, "请先选择文件夹")
            return {'CANCELLED'}
        skin = detect_skin_name_from_folder(folder)
        if skin:
            context.scene.valorant_skin_name = skin
            self.report({"INFO"}, f"已识别枪皮名称: {skin}")
        else:
            self.report({"WARNING"}, "未能识别枪皮名称")
        return {'FINISHED'}


class VALORANT_OT_ImportAllPSK(bpy.types.Operator):
    bl_idname = "valorant.import_all_psk"
    bl_label = "一键导入"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        folder = scene.valorant_import_folder
        if not folder or not os.path.isdir(folder):
            self.report({"ERROR"}, "文件夹无效")
            return {'CANCELLED'}
        files = find_psk_files(folder)
        if not files:
            self.report({"WARNING"}, "没有 PSK/PSKX 文件")
            return {'CANCELLED'}
        op_name = find_psk_import_operator()
        if not op_name:
            force_reenable_psk_addon()
            op_name = find_psk_import_operator()
        if not op_name:
            self.report({"ERROR"}, "PSK 操作符未注册")
            return {'CANCELLED'}
        log(f"使用导入操作符: {op_name}")
        clear_current_scene()
        ok = 0
        fail = 0
        for i, filepath in enumerate(files):
            try:
                call_psk_import(op_name, filepath)
                ok += 1
                log(f"已导入 [{i+1}/{len(files)}]: {os.path.basename(filepath)}")
            except Exception as e:
                fail += 1
                log(f"导入失败 [{i+1}/{len(files)}]: {e}")
        auto_skin = detect_skin_name_from_folder(folder)
        if auto_skin:
            scene.valorant_skin_name = auto_skin
        msg = f"导入完成：成功 {ok} 个，失败 {fail} 个"
        if auto_skin:
            msg += f"，枪皮: {auto_skin}"

        if getattr(scene, "valorant_auto_process_export", False):
            log("=" * 60)
            log("检测到「导入后自动处理并导出」已开启")
            log("=" * 60)
            try:
                log(">>> 开始一键处理模型...")
                process_results = process_all_in_one()
                for line in process_results:
                    log(f"  [一键处理] {line}")
                msg += "；处理✓"
            except Exception as e:
                import traceback
                traceback.print_exc()
                log(f"一键处理失败: {e}")
                msg += "；处理✗"
                self.report({"WARNING"}, msg)
                return {'FINISHED'}
            try:
                log(">>> 开始一键导出模型...")
                export_results = process_export_all()
                for line in export_results:
                    log(f"  [一键导出] {line}")
                msg += "；导出✓"
            except Exception as e:
                import traceback
                traceback.print_exc()
                log(f"一键导出失败: {e}")
                msg += "；导出✗"
                self.report({"WARNING"}, msg)
                return {'FINISHED'}
            log("=" * 60)
            log("自动处理并导出完成")
            log("=" * 60)

        self.report({"WARNING"} if fail > 0 else {"INFO"}, msg)
        return {'FINISHED'}


class VALORANT_OT_ClearScene(bpy.types.Operator):
    bl_idname = "valorant.clear_scene"
    bl_label = "一键清理"

    def execute(self, context):
        try:
            clear_current_scene()
            self.report({"INFO"}, "场景已清空（包括 Export 子集合）")
        except Exception as e:
            self.report({"ERROR"}, f"清理失败: {e}")
        return {'FINISHED'}


class VALORANT_OT_ProcessRemoveStaticModifiers(bpy.types.Operator):
    bl_idname = "valorant.process_remove_static_modifiers"
    bl_label = "删除静态修改器"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = process_remove_static_modifiers()
        self.report({"INFO"}, f"已删除 {removed} 个修改器")
        return {'FINISHED'}


class VALORANT_OT_ProcessDeleteAccessories(bpy.types.Operator):
    bl_idname = "valorant.process_delete_accessories"
    bl_label = "删除多余配件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        deleted = process_delete_accessories()
        self.report({"INFO"}, f"已删除 {len(deleted)} 个对象")
        return {'FINISHED'}


class VALORANT_OT_ProcessMoveToCollection(bpy.types.Operator):
    bl_idname = "valorant.process_move_to_collection"
    bl_label = "放入 Export 集合"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        moved = process_move_to_export_collection()
        self.report({"INFO"}, f"已移动 {moved} 个对象")
        return {'FINISHED'}


class VALORANT_OT_ProcessGunSpecific(bpy.types.Operator):
    bl_idname = "valorant.process_gun_specific"
    bl_label = "执行针对模型处理"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            results = process_gun_specific()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"失败: {e}")
            return {'CANCELLED'}
        self.report({"INFO"}, f"完成，共 {len(results)} 条")
        return {'FINISHED'}


class VALORANT_OT_ProcessCreateMaterials(bpy.types.Operator):
    bl_idname = "valorant.process_create_materials"
    bl_label = "创建材质"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            stats, details = process_create_materials()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"失败: {e}")
            return {'CANCELLED'}

        if not stats['texture_folder']:
            self.report({"ERROR"},
                "未找到 DF/NM/MRS/AEM 贴图。\n"
                "请确认模型文件夹下（或同级 Materials）存在贴图")
            return {'CANCELLED'}

        if stats['texture_sets'] == 0:
            self.report({"ERROR"}, "没有可用的贴图集")
            return {'CANCELLED'}

        for action, mat_name, extra in details:
            if action == '处理':
                log(f"  [处理] {mat_name}  ←  {extra}")
            else:
                log(f"  [{action}] {mat_name}: {extra}")

        msg = (f"处理 {stats['processed']} 个材质，"
               f"跳过特殊 {stats['skipped_special']}，"
               f"跳过无贴图 {stats['skipped_no_match']}")
        self.report({"INFO"}, msg)
        return {'FINISHED'}


class VALORANT_OT_ProcessFixMaterialSlots(bpy.types.Operator):
    bl_idname = "valorant.process_fix_material_slots"
    bl_label = "修复材质槽"
    bl_description = "按面数占比交换主外观槽与附加语义槽的材质引用"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            swapped, details = process_fix_material_slots()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"失败: {e}")
            return {'CANCELLED'}

        for line in details:
            log(f"  [交换] {line}")

        if swapped == 0:
            self.report({"INFO"}, "所有材质槽面数分布正常，无需交换")
        else:
            self.report({"INFO"}, f"已交换 {swapped} 个材质槽")
        return {'FINISHED'}


class VALORANT_OT_ProcessAll(bpy.types.Operator):
    bl_idname = "valorant.process_all"
    bl_label = "一键处理模型"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            results = process_all_in_one()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"失败: {e}")
            return {'CANCELLED'}
        self.report({"INFO"}, f"完成，共 {len(results)} 项")
        for line in results:
            log(f"  {line}")
        return {'FINISHED'}


class VALORANT_OT_ExportConfigureSend(bpy.types.Operator):
    bl_idname = "valorant.export_configure_send"
    bl_label = "配置 Send 插件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ok, msg = process_configure_send2ue()
        if ok:
            self.report({"INFO"}, f"配置完成: {msg}")
        else:
            self.report({"ERROR"}, f"失败: {msg}")
        return {'FINISHED'}


class VALORANT_OT_ExportScaleUp(bpy.types.Operator):
    bl_idname = "valorant.export_scale_up"
    bl_label = "静态放大 ×100"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count, msg = process_static_scale_up()
        self.report({"INFO"}, msg)
        return {'FINISHED'}


class VALORANT_OT_ExportToUE(bpy.types.Operator):
    bl_idname = "valorant.export_to_ue"
    bl_label = "导出到 UE"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ok, msg = process_send_to_ue()
        if ok:
            self.report({"INFO"}, f"成功: {msg}")
        else:
            self.report({"ERROR"}, f"失败: {msg}")
        return {'FINISHED'}


class VALORANT_OT_ExportScaleDown(bpy.types.Operator):
    bl_idname = "valorant.export_scale_down"
    bl_label = "静态缩小 ×0.01"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count, msg = process_static_scale_down()
        self.report({"INFO"}, msg)
        return {'FINISHED'}


class VALORANT_OT_ExportSaveBlend(bpy.types.Operator):
    bl_idname = "valorant.export_save_blend"
    bl_label = "保存 Blender 文件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ok, msg = process_save_blend()
        if ok:
            self.report({"INFO"}, msg)
        else:
            self.report({"ERROR"}, msg)
        return {'FINISHED'}


class VALORANT_OT_ExportAll(bpy.types.Operator):
    bl_idname = "valorant.export_all"
    bl_label = "一键导出模型"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            results = process_export_all()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"失败: {e}")
            return {'CANCELLED'}
        self.report({"INFO"}, f"完成，共 {len(results)} 项")
        for line in results:
            log(f"  {line}")
        return {'FINISHED'}


class VALORANT_OT_InstallPSKAddon(bpy.types.Operator):
    bl_idname = "valorant.install_psk_addon"
    bl_label = "安装PSK插件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if is_psk_addon_installed():
            self.report({"INFO"}, "PSK 插件已经安装")
            return {"FINISHED"}
        ok, msg = ensure_online_access()
        if not ok:
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}
        self.report({"INFO"}, msg)
        success, message = install_psk_addon()
        if success:
            self.report({"INFO"}, "PSK 插件安装成功！")
            global _last_psk_state, _last_send_state
            _last_psk_state = True
            _last_send_state = is_send2ue_installed()
            _refresh_all_child_panels()
        else:
            self.report({"ERROR"}, f"安装失败: {message}")
        refresh_ui(context)
        return {"FINISHED"}


class VALORANT_OT_OpenPSKWebsite(bpy.types.Operator):
    bl_idname = "valorant.open_psk_website"
    bl_label = "打开插件页面"

    def execute(self, context):
        import webbrowser
        webbrowser.open(PSK_EXTENSION_URL)
        self.report({"INFO"}, "已打开")
        return {"FINISHED"}


class VALORANT_OT_InstallSend2UEAddon(bpy.types.Operator):
    bl_idname = "valorant.install_send2ue_addon"
    bl_label = "安装Send插件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if is_send2ue_installed():
            self.report({"INFO"}, "Send 已经安装")
            return {"FINISHED"}
        ok, msg = ensure_online_access()
        if not ok:
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}
        self.report({"INFO"}, msg)
        success, message = install_send2ue_addon()
        if success:
            self.report({"INFO"}, message)
            global _last_psk_state, _last_send_state
            _last_psk_state = is_psk_addon_installed()
            _last_send_state = True
            _refresh_all_child_panels()
        else:
            self.report({"WARNING"} if "已安装但启用失败" in message else {"ERROR"}, message)
        refresh_ui(context)
        return {"FINISHED"}


class VALORANT_OT_OpenSend2UEGitHub(bpy.types.Operator):
    bl_idname = "valorant.open_send2ue_github"
    bl_label = "打开 GitHub 页面"

    def execute(self, context):
        open_send2ue_github()
        self.report({"INFO"}, "已打开")
        return {"FINISHED"}


# ═════════════════════════════════════════════
#  UI 刷新 + 定时器
# ═════════════════════════════════════════════

def refresh_ui(context):
    try:
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:
        pass


_last_psk_state = None
_last_send_state = None
_panel_timer = None


def _get_child_panels_ordered():
    return [
        VALORANT_PT_PSKInstall,
        VALORANT_PT_SendInstall,
        VALORANT_PT_OneClickImport,
        VALORANT_PT_OneClickProcess,
        VALORANT_PT_ProcessBlock1,
        VALORANT_PT_ProcessBlock2,
        VALORANT_PT_ProcessBlock3,
        VALORANT_PT_ProcessBlock4,
        VALORANT_PT_ProcessBlock5,
        VALORANT_PT_ProcessBlock6,
        VALORANT_PT_OneClickExport,
        VALORANT_PT_ExportBlock1,
        VALORANT_PT_ExportBlock2,
        VALORANT_PT_ExportBlock3,
        VALORANT_PT_ExportBlock4,
        VALORANT_PT_ExportBlock5,
    ]


def _update_dynamic_labels():
    psk_installed = is_psk_addon_installed()
    send_installed = is_send2ue_installed()
    VALORANT_PT_PSKInstall.bl_label = LABEL_PSK_INSTALLED if psk_installed else LABEL_PSK_UNINSTALLED
    VALORANT_PT_SendInstall.bl_label = LABEL_SEND_INSTALLED if send_installed else LABEL_SEND_UNINSTALLED


def _refresh_all_child_panels():
    panels = _get_child_panels_ordered()
    for cls in panels:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _update_dynamic_labels()
    for cls in panels:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            log(f"注册 {cls.__name__} 失败: {e}")


def _panel_check_timer():
    global _last_psk_state, _last_send_state
    try:
        psk_installed = is_psk_addon_installed()
        send_installed = is_send2ue_installed()
        if _last_psk_state != psk_installed or _last_send_state != send_installed:
            _last_psk_state = psk_installed
            _last_send_state = send_installed
            _refresh_all_child_panels()
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
    except Exception as e:
        log(f"定时器错误: {e}")
    return 1.0


# ═════════════════════════════════════════════
#  面板
# ═════════════════════════════════════════════

class VALORANT_PT_Main(bpy.types.Panel):
    bl_label = "无畏契约模型转换"
    bl_idname = "VALORANT_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"

    def draw(self, context):
        pass


class VALORANT_PT_PSKInstall(bpy.types.Panel):
    bl_label = LABEL_PSK_UNINSTALLED
    bl_idname = "VALORANT_PT_psk_install"
    bl_parent_id = "VALORANT_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="TOOL_SETTINGS")

    def draw(self, context):
        layout = self.layout
        installed = is_psk_addon_installed()
        info = get_psk_addon_info() if installed else None
        online_ok = bpy.app.online_access
        row = layout.row()
        row.label(
            text="在线访问: 已开启" if online_ok else "在线访问: 已关闭",
            icon="CHECKMARK" if online_ok else "ERROR",
        )
        layout.separator()
        if installed:
            if info:
                layout.label(text=f"版本: {info['version_str']}", icon="CHECKMARK")
            row = layout.row(align=True)
            row.scale_y = 1.5
            row.alert = True
            row.label(text="PSK插件已安装", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.scale_y = 1.5
            row.operator("valorant.install_psk_addon", text="安装PSK插件", icon="IMPORT")
            layout.operator("valorant.open_psk_website", text="打开插件页面", icon="URL")


class VALORANT_PT_SendInstall(bpy.types.Panel):
    bl_label = LABEL_SEND_UNINSTALLED
    bl_idname = "VALORANT_PT_send_install"
    bl_parent_id = "VALORANT_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="URL")

    def draw(self, context):
        layout = self.layout
        installed = is_send2ue_installed()
        info = get_send2ue_info() if installed else None
        if installed:
            if info:
                layout.label(text=f"版本: {info['version_str']}", icon="CHECKMARK")
            row = layout.row(align=True)
            row.scale_y = 1.5
            row.alert = True
            row.label(text="Send插件已安装", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.scale_y = 1.5
            row.operator("valorant.install_send2ue_addon", text="安装Send插件", icon="IMPORT")
            layout.label(text="UE端需启用：", icon="DOT")
            layout.label(text="• Python Editor Script Plugin")
            layout.label(text="• Editor Scripting Utilities")
            layout.operator("valorant.open_send2ue_github", text="打开 GitHub 页面", icon="URL")


class VALORANT_PT_OneClickImport(bpy.types.Panel):
    bl_label = "一键导入"
    bl_idname = "VALORANT_PT_oneclick_import"
    bl_parent_id = "VALORANT_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="IMPORT")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        folder = scene.valorant_import_folder
        folder_valid = bool(folder) and os.path.isdir(folder)
        psk_count = len(find_psk_files(folder)) if folder_valid else 0

        box1 = layout.box()
        col = box1.column(align=True)
        col.label(text="区块 1：选择模型文件夹", icon="FILE_FOLDER")
        col.separator()
        col.prop(scene, "valorant_import_folder", text="")
        if folder:
            if folder_valid:
                col.label(
                    text=f"✓ 找到 {psk_count} 个 PSK/PSKX 文件" if psk_count > 0 else "未找到 PSK 文件",
                    icon="CHECKMARK" if psk_count > 0 else "ERROR",
                )
            else:
                col.label(text="路径无效", icon="ERROR")

        box2 = layout.box()
        col2 = box2.column(align=True)
        col2.label(text="区块 2：导入配置", icon="PREFERENCES")
        col2.separator()
        row = col2.row(align=True)
        row.prop(scene, "valorant_import_config", expand=True)
        cfg = getattr(scene, "valorant_import_config", "gun")
        col2.label(
            text="按枪械种类创建子集合" if cfg == "gun" else "按枪皮名称创建子集合",
            icon="INFO",
        )
        col2.separator()
        col2.prop(scene, "valorant_auto_process_export", text="导入后自动处理并导出")
        if getattr(scene, "valorant_auto_process_export", False):
            sub = col2.column(align=True)
            sub.scale_y = 0.85
            sub.label(text="↳ 依次执行：一键处理 → 一键导出", icon="PLAY")

        box3 = layout.box()
        col3 = box3.column(align=True)
        col3.label(text="区块 3：导入所有模型", icon="IMPORT")
        col3.separator()
        op_ready = find_psk_import_operator() is not None
        if not op_ready:
            col3.label(text="⚠ PSK 操作符未注册", icon="ERROR")
        row = col3.row(align=True)
        row.scale_y = 1.5
        row.enabled = folder_valid and psk_count > 0
        row.operator("valorant.import_all_psk", text="一键导入", icon="IMPORT")
        row2 = col3.row(align=True)
        row2.scale_y = 1.3
        row2.operator("valorant.clear_scene", text="一键清理", icon="TRASH")

        box4 = layout.box()
        col4 = box4.column(align=True)
        col4.label(text="区块 4：枪皮名称", icon="SORTALPHA")
        col4.separator()
        row = col4.row(align=True)
        row.prop(scene, "valorant_skin_name", text="名称")
        sub = row.row(align=True)
        sub.enabled = folder_valid
        sub.operator("valorant.redetect_skin_name", text="", icon="FILE_REFRESH")


class VALORANT_PT_OneClickProcess(bpy.types.Panel):
    bl_label = "一键处理"
    bl_idname = "VALORANT_PT_oneclick_process"
    bl_parent_id = "VALORANT_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="SHADERFX")

    def draw(self, context):
        layout = self.layout
        skin = context.scene.valorant_skin_name or "未识别"
        row = layout.row(align=True)
        row.scale_y = 1.8
        row.alert = True
        row.operator("valorant.process_all", text="一键处理模型", icon="PLAY")
        layout.label(text=f"当前枪皮: {skin}", icon="INFO")


class VALORANT_PT_ProcessBlock1(bpy.types.Panel):
    bl_label = "① 删除静态网格体修改器"
    bl_idname = "VALORANT_PT_process_block1"
    bl_parent_id = "VALORANT_PT_oneclick_process"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="MODIFIER")

    def draw(self, context):
        self.layout.operator("valorant.process_remove_static_modifiers",
                             text="删除静态修改器", icon="TRASH")


class VALORANT_PT_ProcessBlock2(bpy.types.Panel):
    bl_label = "② 删除多余配件"
    bl_idname = "VALORANT_PT_process_block2"
    bl_parent_id = "VALORANT_PT_oneclick_process"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="TRASH")

    def draw(self, context):
        self.layout.operator("valorant.process_delete_accessories",
                             text="删除多余配件", icon="TRASH")


class VALORANT_PT_ProcessBlock3(bpy.types.Panel):
    bl_label = "③ 放入 Export 集合"
    bl_idname = "VALORANT_PT_process_block3"
    bl_parent_id = "VALORANT_PT_oneclick_process"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="COLLECTION_NEW")

    def draw(self, context):
        cfg = getattr(context.scene, "valorant_import_config", "gun")
        skin = context.scene.valorant_skin_name or "未识别"
        if cfg == "gun":
            self.layout.label(text="按枪械创建子集合", icon="INFO")
        else:
            self.layout.label(text=f"挂饰子集合: {skin}", icon="INFO")
        self.layout.operator("valorant.process_move_to_collection",
                             text="放入 Export 集合", icon="COLLECTION_NEW")


class VALORANT_PT_ProcessBlock4(bpy.types.Panel):
    bl_label = "④ 针对模型处理"
    bl_idname = "VALORANT_PT_process_block4"
    bl_parent_id = "VALORANT_PT_oneclick_process"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="ARMATURE_DATA")

    def draw(self, context):
        self.layout.operator("valorant.process_gun_specific",
                             text="执行针对模型处理", icon="PLAY")


class VALORANT_PT_ProcessBlock5(bpy.types.Panel):
    bl_label = "⑤ 创建材质"
    bl_idname = "VALORANT_PT_process_block5"
    bl_parent_id = "VALORANT_PT_oneclick_process"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="MATERIAL")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        model_folder = scene.valorant_import_folder or ""

        col = layout.column(align=True)
        col.label(text="DF→基础色  NM→法线", icon="INFO")
        col.label(text="MRS→粗糙度  AEM→高光度", icon="INFO")
        col.label(text="跳过 Emissive/Decals 材质", icon="INFO")

        if model_folder and os.path.isdir(model_folder):
            tex_map, found = find_texture_sets(model_folder)
            if tex_map:
                col.label(text=f"✓ 找到 {len(tex_map)} 套贴图", icon="CHECKMARK")
            else:
                col.label(text="未找到 DF/NM/MRS/AEM 贴图", icon="ERROR")
        else:
            col.label(text="请先选择模型文件夹", icon="ERROR")

        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator("valorant.process_create_materials",
                     text="创建材质", icon="MATERIAL")


class VALORANT_PT_ProcessBlock6(bpy.types.Panel):
    bl_label = "⑥ 修复材质槽"
    bl_idname = "VALORANT_PT_process_block6"
    bl_parent_id = "VALORANT_PT_oneclick_process"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="ARROW_LEFTRIGHT")

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="按面数交换主外观槽与附加语义槽", icon="INFO")
        col.label(text="判定: 主槽 <30% 且 最大槽 >60%", icon="INFO")
        col.label(text="适用于主外观面被错误分配到 Emissive 槽", icon="INFO")
        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator("valorant.process_fix_material_slots",
                     text="修复材质槽", icon="ARROW_LEFTRIGHT")


class VALORANT_PT_OneClickExport(bpy.types.Panel):
    bl_label = "一键导出"
    bl_idname = "VALORANT_PT_oneclick_export"
    bl_parent_id = "VALORANT_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="EXPORT")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        skin = scene.valorant_skin_name or "未识别"
        config = getattr(scene, "valorant_import_config", "gun") or "gun"

        row = layout.row(align=True)
        row.scale_y = 1.8
        row.alert = True
        row.operator("valorant.export_all", text="一键导出模型", icon="PLAY")

        col = layout.column(align=True)
        if config == "charm":
            col.label(text="挂饰模式", icon="INFO")
            col.label(text="目标: /Game/Charm/", icon="URL")
        else:
            col.label(text=f"枪皮: {skin}", icon="INFO")
            col.label(text=f"目标: /Game/Gun/{skin}/", icon="URL")


class VALORANT_PT_ExportBlock1(bpy.types.Panel):
    bl_label = "① 配置 Send 插件"
    bl_idname = "VALORANT_PT_export_block1"
    bl_parent_id = "VALORANT_PT_oneclick_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="PREFERENCES")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        skin = scene.valorant_skin_name or "未识别"
        config = getattr(scene, "valorant_import_config", "gun") or "gun"

        if config == "charm":
            layout.label(text="路径: /Game/Charm/", icon="URL")
        else:
            layout.label(text=f"路径: /Game/Gun/{skin}/", icon="URL")
        layout.label(text="勾选 use collections as folders", icon="INFO")
        layout.operator("valorant.export_configure_send",
                        text="配置 Send 插件", icon="PREFERENCES")


class VALORANT_PT_ExportBlock2(bpy.types.Panel):
    bl_label = "② 处理静态模型缩放"
    bl_idname = "VALORANT_PT_export_block2"
    bl_parent_id = "VALORANT_PT_oneclick_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="FULLSCREEN_ENTER")

    def draw(self, context):
        self.layout.operator("valorant.export_scale_up",
                             text="静态放大 ×100", icon="FULLSCREEN_ENTER")


class VALORANT_PT_ExportBlock3(bpy.types.Panel):
    bl_label = "③ 导出到 UE"
    bl_idname = "VALORANT_PT_export_block3"
    bl_parent_id = "VALORANT_PT_oneclick_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="URL")

    def draw(self, context):
        self.layout.operator("valorant.export_to_ue",
                             text="导出到 UE", icon="URL")


class VALORANT_PT_ExportBlock4(bpy.types.Panel):
    bl_label = "④ 恢复静态网格体缩放"
    bl_idname = "VALORANT_PT_export_block4"
    bl_parent_id = "VALORANT_PT_oneclick_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="FULLSCREEN_EXIT")

    def draw(self, context):
        self.layout.operator("valorant.export_scale_down",
                             text="静态缩小 ×0.01", icon="FULLSCREEN_EXIT")


class VALORANT_PT_ExportBlock5(bpy.types.Panel):
    bl_label = "⑤ 保存文件"
    bl_idname = "VALORANT_PT_export_block5"
    bl_parent_id = "VALORANT_PT_oneclick_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "瓦"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="FILE_TICK")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        folder = scene.valorant_import_folder or ""
        skin = scene.valorant_skin_name or ""
        if folder and skin:
            layout.label(text=f"保存到: {os.path.join(folder, skin + '.blend')}", icon="INFO")
        else:
            layout.label(text="请先设置导入文件夹和枪皮名称", icon="ERROR")
        layout.operator("valorant.export_save_blend",
                        text="保存 Blender 文件", icon="FILE_TICK")


# ═════════════════════════════════════════════
#  注册与注销
# ═════════════════════════════════════════════

_OPERATOR_CLASSES = [
    VALORANT_OT_InstallPSKAddon,
    VALORANT_OT_OpenPSKWebsite,
    VALORANT_OT_InstallSend2UEAddon,
    VALORANT_OT_OpenSend2UEGitHub,
    VALORANT_OT_RedetectSkinName,
    VALORANT_OT_ImportAllPSK,
    VALORANT_OT_ClearScene,
    VALORANT_OT_ProcessRemoveStaticModifiers,
    VALORANT_OT_ProcessDeleteAccessories,
    VALORANT_OT_ProcessMoveToCollection,
    VALORANT_OT_ProcessGunSpecific,
    VALORANT_OT_ProcessCreateMaterials,
    VALORANT_OT_ProcessFixMaterialSlots,
    VALORANT_OT_ProcessAll,
    VALORANT_OT_ExportConfigureSend,
    VALORANT_OT_ExportScaleUp,
    VALORANT_OT_ExportToUE,
    VALORANT_OT_ExportScaleDown,
    VALORANT_OT_ExportSaveBlend,
    VALORANT_OT_ExportAll,
]


def register():
    global _panel_timer, _last_psk_state, _last_send_state

    bpy.types.Scene.valorant_import_folder = bpy.props.StringProperty(
        name="模型文件夹", subtype='DIR_PATH', default="",
    )
    bpy.types.Scene.valorant_skin_name = bpy.props.StringProperty(
        name="枪皮名称", default="",
    )
    bpy.types.Scene.valorant_import_config = bpy.props.EnumProperty(
        name="导入配置",
        items=[
            ('gun', "枪械", "按枪械种类创建子集合"),
            ('charm', "挂饰", "按枪皮名称创建子集合"),
        ],
        default='gun',
    )
    bpy.types.Scene.valorant_auto_process_export = bpy.props.BoolProperty(
        name="导入后自动处理并导出",
        description="勾选后，点击一键导入会自动依次执行一键处理和一键导出",
        default=False,
    )

    psk_installed = is_psk_addon_installed()
    send_installed = is_send2ue_installed()
    _last_psk_state = psk_installed
    _last_send_state = send_installed

    _update_dynamic_labels()

    bpy.utils.register_class(VALORANT_PT_Main)
    for cls in _get_child_panels_ordered():
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            log(f"注册子面板 {cls.__name__} 失败: {e}")
    for cls in _OPERATOR_CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            log(f"注册操作符 {cls.__name__} 失败: {e}")

    if _panel_timer is None:
        _panel_timer = bpy.app.timers.register(_panel_check_timer, first_interval=1.0)

    log("插件已注册")


def unregister():
    global _panel_timer
    if _panel_timer is not None:
        try:
            bpy.app.timers.unregister(_panel_timer)
        except Exception:
            pass
        _panel_timer = None

    for cls in reversed(_OPERATOR_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    for cls in reversed(_get_child_panels_ordered()):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    try:
        bpy.utils.unregister_class(VALORANT_PT_Main)
    except Exception:
        pass

    for attr in (
        "valorant_import_folder",
        "valorant_skin_name",
        "valorant_import_config",
        "valorant_auto_process_export",
    ):
        try:
            delattr(bpy.types.Scene, attr)
        except Exception:
            pass

    log("插件已卸载")


if __name__ == "__main__":
    register()