# =========================================================
#  UE - 创建 Overview 预览场景
#  用法：py "路径/create_overview.py"
#
#  依赖 Blender 端命名规则：
#    枪身: GN_{gun_type}_..._Skelmesh  /  EQ_{gun_type}_..._Skelmesh
#    弹匣: GN_{gun_type}_Magazine_...
#    配件: GN_{gun_type}_{RedDot|Silencer}_Skelmesh
# =========================================================

import unreal
import os
import re


# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────

# ★ 皮肤名解析（P0-1，三级 fallback，详见交接文档）：
#   1. SKIN_OVERRIDE / SKIN_NAME 非空 → 强制指定（后门）
#   2. watcher.get_active_skin()      → 最近导入的皮肤（自动路径）
#   3. 磁盘 mtime 扫描 {Content}/Gun/ → 兜底（手动按钮直调时）
#   都失败 → log_err 退出
SKIN_NAME = ""            # 后门：非空时强制指定
SKIN_OVERRIDE = None      # EUW 手动传参通道：c.SKIN_OVERRIDE="XXX"; c.run()
GUN_ROOT = "/Game/Gun"

# mtime 扫描的黑名单子目录（军师意见⑧）：Map/ 只含我们生成的 L_{skin}_Overview，
# 不算皮肤信号。常量以 watcher 侧为准（双端统一），import 失败时用同名兜底
try:
    from auto_material_watcher import SKIN_DIR_BLACKLIST as _SKIN_DIR_BLACKLIST
except Exception:
    _SKIN_DIR_BLACKLIST = ("map",)

TEMPLATE_MAP = "/Engine/Maps/Templates/Template_Default"

GUN_TYPES = ["BasePistol", "AutoShotgun", "Melee", "Burst", "Carbine"]
ACCESSORY_KEYWORDS = ["RedDot", "Silencer"]

# 每把枪的 Y 轴间距（cm）
GUN_SPACING = 100.0

# 弹匣生成时相对枪身的 X 偏移（cm），attach 后作为相对位置保留
MAGAZINE_OFFSET_X = 30.0

# 模型缩放
MESH_SCALE = 0.01

_GUN_ALT = "|".join(GUN_TYPES)
_ACC_ALT = "|".join(ACCESSORY_KEYWORDS)

RE_ACCESSORY = re.compile(
    r"^(?:GN|EQ)_(" + _GUN_ALT + r")_(" + _ACC_ALT + r")(?:_|$)",
    re.IGNORECASE,
)
RE_MAGAZINE = re.compile(
    r"^(?:GN|EQ)_(" + _GUN_ALT + r")_Magazine(?:_|$)",
    re.IGNORECASE,
)
RE_BODY = re.compile(
    r"^(?:GN|EQ)_(" + _GUN_ALT + r")(?:_|$)",
    re.IGNORECASE,
)


def log(msg):
    unreal.log(f"[Overview] {msg}")


def log_warn(msg):
    unreal.log_warning(f"[Overview] {msg}")


def log_err(msg):
    unreal.log_error(f"[Overview] {msg}")


# ─────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────

def get_level_subsystem():
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def get_actor_subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def get_all_actors():
    return get_actor_subsystem().get_all_level_actors()


def delete_actor(actor):
    try:
        get_actor_subsystem().destroy_actor(actor)
        return True
    except Exception as e:
        log_warn(f"删除 actor 失败: {e}")
        return False


def suppress_auto_material(seconds=180.0):
    try:
        import auto_material_watcher as w
        w.suppress(seconds)
        return True
    except Exception as e:
        log_warn(f"抑制自动材质失败（忽略）: {e}")
        return False


# ─────────────────────────────────────────────
# 皮肤名解析（P0-1）
# ─────────────────────────────────────────────
# 皮肤名以局部变量+参数传递（军师意见⑥）：main() 每次执行都重新解析，
# 同一会话连续两次 run() 不会读到旧皮肤；不用模块级跨调用缓存。


def _latest_skin_dir_on_disk():
    """扫 {Content}/Gun/*/ 取最近改动的候选皮肤目录

    候选判定（军师意见⑧ + Q8 修正，白名单为主）：
      - registry 查到资产：必须含 SkeletalMesh 才算候选；
      - registry 查询为空（未扫完/无资产）：★ 保守排除，宁可漏判不误判
        （军师 Q8：漏判无损失——registry 就绪后总数变化会再触发；
        误判会写入脏皮肤名，用户还得手动清）；
      - 黑名单目录（Map/ 等产物目录）任何分支下都排除。
    """
    try:
        content_dir = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_content_dir()
        )
        gun_root = os.path.join(content_dir, "Gun")
        if not os.path.isdir(gun_root):
            return None

        registry = None
        best_name, best_mtime = None, -1.0
        for name in os.listdir(gun_root):
            d = os.path.join(gun_root, name)
            if not os.path.isdir(d):
                continue
            if name.lower() in _SKIN_DIR_BLACKLIST:
                continue

            # 白名单：确认含 SkeletalMesh 才算候选；查询为空 → 保守排除（Q8）
            try:
                if registry is None:
                    registry = unreal.AssetRegistryHelpers.get_asset_registry()
                assets = registry.get_assets_by_path(
                    f"{GUN_ROOT}/{name}", recursive=True
                )
                if not any(
                    str(a.asset_class_path.asset_name) == "SkeletalMesh"
                    for a in assets
                ):
                    continue  # 无资产（未扫完）或有资产但无骨架网格 → 都排除
            except Exception:
                continue  # 查询异常 → 同样保守排除

            latest = -1.0
            for root, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if x.lower() not in _SKIN_DIR_BLACKLIST]
                for f in files:
                    if not f.lower().endswith(".uasset"):
                        continue
                    try:
                        m = os.path.getmtime(os.path.join(root, f))
                    except OSError:
                        continue
                    if m > latest:
                        latest = m
            if latest > best_mtime:
                best_mtime, best_name = latest, name
        return best_name
    except Exception as e:
        log_warn(f"mtime 扫描失败: {e}")
        return None


def resolve_skin():
    """三级解析当前皮肤名（优先级见配置区注释，军师意见 C）"""
    # 1) 显式覆盖
    override = SKIN_OVERRIDE or SKIN_NAME
    if override:
        log(f"皮肤（显式指定）: {override}")
        return override

    # 2) watcher 记录的最近导入皮肤
    try:
        import auto_material_watcher as w
        skin = w.get_active_skin()
        if skin:
            log(f"皮肤（watcher 记录）: {skin}")
            return skin
    except Exception:
        pass

    # 3) 磁盘 mtime 兜底
    skin = _latest_skin_dir_on_disk()
    if skin:
        log(f"皮肤（mtime 兜底）: {skin}")
        return skin

    return ""


# ─────────────────────────────────────────────
# 1. 创建 / 打开 Overview 关卡
# ─────────────────────────────────────────────

def create_overview_level(skin):
    map_dir = f"{GUN_ROOT}/{skin}/Map"
    map_name = f"L_{skin}_Overview"
    full_path = f"{map_dir}/{map_name}"

    les = get_level_subsystem()

    if unreal.EditorAssetLibrary.does_asset_exist(full_path):
        log(f"关卡已存在，加载: {full_path}")
        les.load_level(full_path)
        return full_path

    log(f"从模板创建: {TEMPLATE_MAP}")
    try:
        les.new_level_from_template(full_path, TEMPLATE_MAP)
        log(f"关卡创建成功: {full_path}")
        return full_path
    except Exception as e:
        log_err(f"new_level_from_template 失败: {e}")
        try:
            les.new_level(full_path)
            log(f"已创建空关卡（模板方法失败）")
            return full_path
        except Exception as e2:
            log_err(f"new_level 也失败: {e2}")
            return None


# ─────────────────────────────────────────────
# 2. 删除地板
# ─────────────────────────────────────────────

def delete_floors():
    deleted = 0
    for actor in get_all_actors():
        try:
            name = actor.get_name()
            class_name = actor.get_class().get_name()

            is_floor_by_name = any(kw in name.lower() for kw in ("floor", "ground"))
            is_template_floor = (
                class_name == "StaticMeshActor"
                and "template" in name.lower()
            )

            if is_floor_by_name or is_template_floor:
                log(f"  删除地板: {name} ({class_name})")
                delete_actor(actor)
                deleted += 1
        except Exception as e:
            log_warn(f"检查 actor 失败: {e}")
    log(f"共删除 {deleted} 个地板 actor")


# ─────────────────────────────────────────────
# 3. 创建后期盒子
# ─────────────────────────────────────────────

def create_post_process_volume():
    try:
        ppv = get_actor_subsystem().spawn_actor_from_class(
            unreal.PostProcessVolume, unreal.Vector(0, 0, 0)
        )
        if ppv is None:
            log_err("创建 PostProcessVolume 失败")
            return None

        ppv.set_actor_label("Overview_PostProcess")
        ppv.set_editor_property("unbound", True)

        try:
            settings = ppv.get_editor_property("settings")

            settings.set_editor_property("override_auto_exposure_min_brightness", True)
            settings.set_editor_property("auto_exposure_min_brightness", 1.0)
            settings.set_editor_property("override_auto_exposure_max_brightness", True)
            settings.set_editor_property("auto_exposure_max_brightness", 1.0)

            settings.set_editor_property("override_auto_exposure_bias", True)
            settings.set_editor_property("auto_exposure_bias", 1.0)

            ppv.set_editor_property("settings", settings)
            log("后期盒子设置完成（曝光 min/max/bias=1）")
        except Exception as e:
            log_warn(f"设置后期盒子参数失败: {e}")

        return ppv
    except Exception as e:
        log_err(f"创建后期盒子异常: {e}")
        return None


# ─────────────────────────────────────────────
# 4. 扫描资产
# ─────────────────────────────────────────────

def scan_assets(skin):
    root = f"{GUN_ROOT}/{skin}"

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    try:
        all_assets = registry.get_assets_by_path(root, recursive=True)
    except Exception as e:
        log_err(f"扫描失败: {e}")
        return {}, [], []

    guns = {}
    magazines = []
    accessories = []

    for a in all_assets:
        try:
            cls = str(a.asset_class_path.asset_name)
        except Exception:
            continue
        if cls != "SkeletalMesh":
            continue

        name = str(a.asset_name)
        path = str(a.package_name)

        m = RE_ACCESSORY.match(name)
        if m:
            gun_type = m.group(1)
            source_kw = m.group(2)
            for g in GUN_TYPES:
                if g.lower() == gun_type.lower():
                    gun_type = g
                    break
            for kw in ACCESSORY_KEYWORDS:
                if kw.lower() == source_kw.lower():
                    source_kw = kw
                    break
            accessories.append((gun_type, source_kw, path, name))
            continue

        m = RE_MAGAZINE.match(name)
        if m:
            gun_type = m.group(1)
            for g in GUN_TYPES:
                if g.lower() == gun_type.lower():
                    gun_type = g
                    break
            magazines.append((gun_type, path, name))
            continue

        m = RE_BODY.match(name)
        if m:
            gun_type = m.group(1)
            for g in GUN_TYPES:
                if g.lower() == gun_type.lower():
                    gun_type = g
                    break
            if gun_type not in guns:
                guns[gun_type] = (path, name)
            continue

    log(f"扫描结果：")
    log(f"  枪身 {len(guns)}: {sorted(guns.keys())}")
    log(f"  弹匣 {len(magazines)}")
    log(f"  配件 {len(accessories)}")
    for gt, kw, _, n in accessories:
        log(f"    {gt} / {kw} → {n}")

    return guns, magazines, accessories


# ─────────────────────────────────────────────
# 5. 场景放置
# ─────────────────────────────────────────────

def spawn_skeletal_mesh_actor(mesh_path, location, label=None):
    try:
        mesh = unreal.EditorAssetLibrary.load_asset(mesh_path)
        if mesh is None:
            log_warn(f"加载失败: {mesh_path}")
            return None

        actor = get_actor_subsystem().spawn_actor_from_class(
            unreal.SkeletalMeshActor, location
        )
        if actor is None:
            return None

        try:
            comp = actor.skeletal_mesh_component
            comp.set_skinned_asset_and_update(mesh)
        except Exception:
            try:
                comp.set_skeletal_mesh(mesh)
            except Exception as e:
                log_warn(f"设置 mesh 失败: {e}")

        actor.set_actor_scale3d(unreal.Vector(MESH_SCALE, MESH_SCALE, MESH_SCALE))

        if label:
            actor.set_actor_label(label)

        return actor
    except Exception as e:
        log_err(f"spawn 失败 {mesh_path}: {e}")
        return None


def zero_actor_relative_transform(actor):
    try:
        actor.set_actor_relative_location(unreal.Vector(0, 0, 0), False, False)
        actor.set_actor_relative_rotation(unreal.Rotator(0, 0, 0), False, False)
        return "actor.set_actor_relative_location+rotation"
    except Exception as e1:
        log_warn(f"    actor 级归零失败: {e1}")

    try:
        comp = actor.root_component
        comp.set_relative_location(unreal.Vector(0, 0, 0), False, False)
        comp.set_relative_rotation(unreal.Rotator(0, 0, 0), False, False)
        return "root_component.set_relative_location+rotation"
    except Exception as e2:
        log_warn(f"    comp 级归零失败: {e2}")

    try:
        comp = actor.root_component
        t = unreal.Transform()
        t.translation = unreal.Vector(0, 0, 0)
        t.rotation = unreal.Quat(0, 0, 0, 1)
        t.scale3d = unreal.Vector(1, 1, 1)
        comp.set_relative_transform(t, False, False)
        return "root_component.set_relative_transform(identity)"
    except Exception as e3:
        log_warn(f"    transform 级归零失败: {e3}")

    try:
        actor.k2_set_actor_relative_location(unreal.Vector(0, 0, 0), False, False)
        actor.k2_set_actor_relative_rotation(unreal.Rotator(0, 0, 0), False, False)
        return "k2_set_actor_relative_*"
    except Exception as e4:
        log_warn(f"    k2 归零失败: {e4}")

    return None


def attach_to_gun(actor, gun_actor, gun_type, tag, zero_transform=True):
    """把 actor attach 到 gun_actor

    参数：
      tag: 日志显示用的名字（如 'Magazine'、'RedDot'）
      zero_transform:
        True  → attach 后归零相对变换（配件用，位置由 mesh 数据决定）
        False → 保留原世界相对位置（弹匣用，保留生成时的 X 偏移）
    """
    attached = False

    # 首选：actor 级 attach + socket='None' + KEEP_WORLD
    try:
        ok = actor.attach_to_actor(
            gun_actor,
            "None",
            unreal.AttachmentRule.KEEP_WORLD,
            unreal.AttachmentRule.KEEP_WORLD,
            unreal.AttachmentRule.KEEP_WORLD,
            False,
        )
        if ok:
            attached = True
    except Exception as e:
        log_warn(f"    actor attach 异常: {e}")

    # 兜底 1：component 级
    if not attached:
        try:
            acc_comp = actor.skeletal_mesh_component
            gun_comp = gun_actor.skeletal_mesh_component
            ok = acc_comp.attach_to_component(
                gun_comp,
                "None",
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                False,
            )
            if ok:
                attached = True
        except Exception as e:
            log_warn(f"    component attach 异常: {e}")

    # 兜底 2：socket='root'
    if not attached:
        try:
            ok = actor.attach_to_actor(
                gun_actor,
                "root",
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                False,
            )
            if ok:
                attached = True
        except Exception as e:
            log_warn(f"    actor attach(root) 异常: {e}")

    if not attached:
        log_err(f"    attach 失败: {gun_type} ← {tag}")
        return False

    # 归零相对变换
    if zero_transform:
        method = zero_actor_relative_transform(actor)
        if method:
            log(f"    attach 成功: {gun_type} ← {tag}，归零方式={method}")
        else:
            log_warn(f"    attach 成功但归零全部失败: {gun_type} ← {tag}")
    else:
        log(f"    attach 成功: {gun_type} ← {tag}（保留相对位置）")

    return True


def process_place_assets(guns, magazines, accessories):
    """按枪械类型顺序 Y 轴排列"""
    scene_gun_actors = {}

    ordered_guns = [(gt, guns[gt]) for gt in GUN_TYPES if gt in guns]
    for gt in guns:
        if gt not in GUN_TYPES:
            ordered_guns.append((gt, guns[gt]))

    # 1) 枪身
    for i, (gun_type, (path, name)) in enumerate(ordered_guns):
        base_y = i * GUN_SPACING
        log("")
        log(f"  位置 {i}  [{gun_type}]  y={base_y/100:.2f}m")

        gun_loc = unreal.Vector(0.0, base_y, 0.0)
        gun_actor = spawn_skeletal_mesh_actor(path, gun_loc, label=f"GUN_{gun_type}")
        if gun_actor:
            scene_gun_actors[gun_type] = gun_actor
            log(f"    枪身: {name}")

    # 2) 弹匣 —— 生成后 attach 到对应枪身（保留 X 偏移）
    for gun_type, path, name in magazines:
        i = next((idx for idx, (gt, _) in enumerate(ordered_guns) if gt == gun_type), None)
        if i is None:
            continue
        base_y = i * GUN_SPACING
        mag_loc = unreal.Vector(MAGAZINE_OFFSET_X, base_y, 0.0)
        actor = spawn_skeletal_mesh_actor(path, mag_loc, label=f"MAG_{gun_type}")
        if actor is None:
            continue

        log(f"    弹匣: {name}")

        gun_actor = scene_gun_actors.get(gun_type)
        if gun_actor is None:
            log_warn(f"    未找到 {gun_type} 的枪身，弹匣保持独立")
            continue

        # ★ 弹匣不归零，保留原 X 偏移
        attach_to_gun(actor, gun_actor, gun_type, "Magazine", zero_transform=True)

    # 3) 配件 —— attach 到对应枪身（归零）
    for gun_type, source_kw, path, name in accessories:
        i = next((idx for idx, (gt, _) in enumerate(ordered_guns) if gt == gun_type), None)
        base_y = (i * GUN_SPACING) if i is not None else 0.0

        loc = unreal.Vector(0.0, base_y, 0.0)
        actor = spawn_skeletal_mesh_actor(path, loc, label=f"ACC_{gun_type}_{source_kw}")
        if actor is None:
            continue

        log(f"    配件: {name}")

        gun_actor = scene_gun_actors.get(gun_type)
        if gun_actor is None:
            log_warn(f"    未找到 {gun_type} 的枪身，配件保持独立")
            continue

        attach_to_gun(actor, gun_actor, gun_type, source_kw, zero_transform=True)


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

def main():
    log("=" * 60)
    log("创建 Overview 预览场景 - 开始")
    log("=" * 60)

    # ★ 每次执行都重新解析（军师意见⑥：局部变量+参数传递，无跨调用缓存）
    skin = resolve_skin()
    if not skin:
        log_err("未解析到皮肤名（显式指定 / watcher 记录 / mtime 扫描均失败）。")
        log_err("可能原因：编辑器刚启动，AssetRegistry 尚未扫完（白名单宁可漏判不误判），请稍候重试。")
        log_err("强制指定方法: import create_overview as c; c.SKIN_OVERRIDE='皮肤名'; c.run()")
        return
    log(f"目标皮肤: {skin}")

    suppress_auto_material(180.0)

    map_path = create_overview_level(skin)
    if not map_path:
        log_err("关卡创建失败，终止")
        return

    log("")
    log("── 删除地板 ──")
    delete_floors()

    log("")
    log("── 创建后期盒子 ──")
    create_post_process_volume()

    log("")
    log("── 扫描资产 ──")
    guns, magazines, accessories = scan_assets(skin)
    if not guns:
        log_warn("未找到枪械主体，退出")
        return

    log("")
    log("── 放置模型 ──")
    process_place_assets(guns, magazines, accessories)

    log("")
    log("── 保存关卡 ──")
    try:
        get_level_subsystem().save_current_level()
        log(f"已保存: {map_path}")
    except Exception as e:
        log_warn(f"保存失败: {e}")

    log("")
    log("=" * 60)
    log("完成")
    log("=" * 60)


# ─────────────────────────────────────────────
# 对外入口（供 EUW 按钮 / auto_material_watcher 调用）
# ─────────────────────────────────────────────

def run():
    """手动执行一次预览地图创建（不启动监听）"""
    main()


# 与 auto_material_watcher.run_once 风格对齐的别名
run_once = run


if __name__ == "__main__":
    main()