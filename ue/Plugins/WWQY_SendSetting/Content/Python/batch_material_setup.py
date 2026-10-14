# =========================================================
#  Valorant 枪皮材质自动配置（UE 5.8）
#
#  功能：
#    1. 修正贴图压缩：MRS/AEM → TC_MASKS 且去掉 sRGB
#    2. 为每个材质创建 Material Instance（基于母材质）
#    3. 设置 DF/NM/MRS/AEM 贴图参数
#    4. 替换所有 Mesh 上的原材质引用
#
#  用法：py "路径/batch_material_setup.py"
# =========================================================

import unreal


# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────

SCAN_ROOT = "/Game/"
MASTER_MATERIAL_PATH = "/WWQY_SendSetting/Materials/Ma_Valorant_Master"

# 母材质的资产名（从 MASTER_MATERIAL_PATH 提取），用于跳过自身
MASTER_ASSET_NAME = MASTER_MATERIAL_PATH.rsplit("/", 1)[-1]

TEXTURE_PARAM_MAP = {
    "DF":  "BaseColorTex",
    "NM":  "NormalTex",
    "MRS": "MRSTex",
    "AEM": "AEMTex",
}

SKIP_KEYWORDS = ["emissive", "decals", "decal"]
STRIP_SUFFIXES = ["_MI", "_Mat", "_Material", "_Inst", "_Inst_0"]

TEXTURE_COMPRESSION_MAP = {
    "DF":  ("default",   True),
    "NM":  ("normalmap", False),
    "MRS": ("masks",     False),
    "AEM": ("masks",     False),
}

_COMP_MAP = {
    "masks":     unreal.TextureCompressionSettings.TC_MASKS,
    "default":   unreal.TextureCompressionSettings.TC_DEFAULT,
    "normalmap": unreal.TextureCompressionSettings.TC_NORMALMAP,
}


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def log(msg):
    unreal.log(f"[Valorant材质] {msg}")


def log_warn(msg):
    unreal.log_warning(f"[Valorant材质] {msg}")


def log_err(msg):
    unreal.log_error(f"[Valorant材质] {msg}")


def should_skip(mat_name, mat_path=None):
    """判断材质是否应该跳过

    跳过条件：
      1. 名字含 Emissive / Decals / Decal
      2. 是母材质自己（Ma_Valorant_Master）
    """
    low = mat_name.lower()
    if any(kw in low for kw in SKIP_KEYWORDS):
        return True
    if mat_name == MASTER_ASSET_NAME:
        return True
    return False


def strip_suffix(name):
    for suf in STRIP_SUFFIXES:
        if name.endswith(suf):
            return name[:-len(suf)]
    return name


def get_assets_by_class(path, class_name):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    try:
        assets = registry.get_assets_by_path(path, recursive=True)
    except Exception as e:
        log_err(f"扫描失败 {path}: {e}")
        return []
    result = []
    for a in assets:
        try:
            if str(a.asset_class_path.asset_name) == class_name:
                result.append(a)
        except Exception:
            continue
    return result


# ═════════════════════════════════════════════
#  贴图压缩设置
# ═════════════════════════════════════════════

def configure_texture_compression(tex, suffix):
    if suffix not in TEXTURE_COMPRESSION_MAP:
        return False, ""

    target_comp_str, target_srgb = TEXTURE_COMPRESSION_MAP[suffix]
    target_comp = _COMP_MAP[target_comp_str]

    try:
        old_comp = tex.get_editor_property("compression_settings")
    except Exception:
        old_comp = None
    try:
        old_srgb = tex.get_editor_property("srgb")
    except Exception:
        old_srgb = None

    changes = []

    if old_comp != target_comp:
        try:
            tex.set_editor_property("compression_settings", target_comp)
            changes.append(f"comp={target_comp_str}")
        except Exception as e:
            log_warn(f"    设置压缩失败: {e}")

    if old_srgb != target_srgb:
        try:
            tex.set_editor_property("srgb", target_srgb)
            changes.append(f"sRGB={target_srgb}")
        except Exception as e:
            log_warn(f"    设置 sRGB 失败: {e}")

    if changes:
        try:
            unreal.EditorAssetLibrary.save_loaded_asset(tex, False)
        except Exception:
            pass
        return True, " ".join(changes)

    return False, ""


def fix_all_texture_compression(texture_assets):
    log("")
    log("── 修正贴图压缩设置 ──")

    changed = 0
    total = 0

    for tex_data in texture_assets:
        tex_name = str(tex_data.asset_name)
        tex = unreal.EditorAssetLibrary.load_asset(str(tex_data.package_name))
        if tex is None:
            continue

        suffix = None
        for suf in ("DF", "NM", "MRS", "AEM"):
            if tex_name.endswith("_" + suf):
                suffix = suf
                break

        if suffix is None:
            continue

        total += 1
        was_changed, desc = configure_texture_compression(tex, suffix)
        if was_changed:
            changed += 1
            log(f"  [改] {tex_name}  {desc}")

    log(f"  共检查 {total} 张，修改 {changed} 张，跳过 {total - changed} 张")
    return changed, total


# ═════════════════════════════════════════════
#  贴图索引
# ═════════════════════════════════════════════

def build_texture_index(texture_assets):
    index = {}
    for tex_data in texture_assets:
        tex = unreal.EditorAssetLibrary.load_asset(str(tex_data.package_name))
        if tex is None:
            continue
        try:
            index[tex.get_name()] = tex.get_path_name()
        except Exception:
            continue
    return index


def find_texture(base_name, suffix, texture_index):
    target = f"{base_name}_{suffix}"
    if target in texture_index:
        return unreal.EditorAssetLibrary.load_asset(texture_index[target])

    target_low = target.lower()
    for name, path in texture_index.items():
        low = name.lower()
        if low == target_low:
            return unreal.EditorAssetLibrary.load_asset(path)
        if low.startswith(target_low):
            return unreal.EditorAssetLibrary.load_asset(path)
    return None


# ═════════════════════════════════════════════
#  MI 创建
# ═════════════════════════════════════════════

def get_or_create_mi(base_name, master, dest_path):
    mi_name = f"MI_{base_name}"
    mi_path = f"{dest_path.rstrip('/')}/{mi_name}"

    # 先判断存在再加载，避免 LoadAsset failed 报错
    if unreal.EditorAssetLibrary.does_asset_exist(mi_path):
        existing = unreal.EditorAssetLibrary.load_asset(mi_path)
        if existing is not None:
            try:
                if isinstance(existing, unreal.MaterialInstanceConstant):
                    # ★ 校验父材质（母材质换路径后需强制刷新）
                    need_reset = False
                    try:
                        cur_parent = existing.get_editor_property("parent")
                        if cur_parent is None:
                            need_reset = True
                        elif get_object_path(cur_parent) != get_object_path(master):
                            need_reset = True
                    except Exception:
                        need_reset = True

                    if need_reset:
                        try:
                            unreal.MaterialEditingLibrary.set_material_instance_parent(
                                existing, master
                            )
                            log(f"    更新 MI 父材质: {mi_name}")
                            return existing, True
                        except Exception as e:
                            log_warn(f"    更新 MI 父材质失败: {e}")
                    return existing, False
            except Exception:
                pass

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    factory = unreal.MaterialInstanceConstantFactoryNew()
    mi = tools.create_asset(
        asset_name=mi_name,
        package_path=dest_path.rstrip('/'),
        asset_class=unreal.MaterialInstanceConstant,
        factory=factory,
    )
    if mi is None:
        log_err(f"    创建 MI 失败: {mi_name}")
        return None, False

    try:
        unreal.MaterialEditingLibrary.set_material_instance_parent(mi, master)
    except Exception as e:
        log_err(f"    设置父材质失败: {e}")
        return None, False

    return mi, True


def get_current_texture_param(mi, param_name):
    try:
        return unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(
            mi, param_name
        )
    except Exception:
        return None


def get_object_path(obj):
    if obj is None:
        return ""
    try:
        return obj.get_path_name()
    except Exception:
        return ""


def set_texture_param_if_needed(mi, param_name, texture):
    """如果参数已指向目标贴图就跳过

    返回 "set" / "skip" / "fail"
    """
    current = get_current_texture_param(mi, param_name)
    if current is not None:
        cur_path = get_object_path(current)
        target_path = get_object_path(texture)
        if cur_path and cur_path == target_path:
            return "skip"

    try:
        unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
            mi, param_name, texture
        )
        return "set"
    except Exception as e:
        log_warn(f"    设置 {param_name} 异常: {e}")
        return "fail"


# ═════════════════════════════════════════════
#  Mesh 材质替换
# ═════════════════════════════════════════════

def replace_materials_in_mesh(mesh, mat_map, is_static_mesh):
    changed = 0
    skipped = 0
    try:
        if is_static_mesh:
            slots = mesh.get_editor_property("static_materials")
        else:
            slots = mesh.get_editor_property("materials")
    except Exception as e:
        log_warn(f"    读取材质槽失败: {e}")
        return 0, 0

    new_slots = []
    for slot in slots:
        try:
            slot_name = slot.get_editor_property("material_slot_name")
            old_mat = slot.get_editor_property("material_interface")
        except Exception:
            new_slots.append(slot)
            continue

        new_mat = old_mat

        if old_mat is not None:
            try:
                mat_name = old_mat.get_name()
            except Exception:
                mat_name = ""

            if mat_name.startswith("MI_"):
                skipped += 1
            else:
                old_full = get_object_path(old_mat)
                if old_full and old_full in mat_map:
                    new_mat = mat_map[old_full]
                    changed += 1

        if is_static_mesh:
            new_slot = unreal.StaticMaterial()
        else:
            new_slot = unreal.SkeletalMaterial()
        new_slot.set_editor_property("material_slot_name", slot_name)
        new_slot.set_editor_property("material_interface", new_mat)
        new_slots.append(new_slot)

    if changed:
        if is_static_mesh:
            mesh.set_editor_property("static_materials", new_slots)
        else:
            mesh.set_editor_property("materials", new_slots)

    return changed, skipped


# ═════════════════════════════════════════════
#  主流程
# ═════════════════════════════════════════════

def main():
    log("=" * 60)
    log("Valorant 材质自动配置 - 开始")
    log("=" * 60)

    master = unreal.EditorAssetLibrary.load_asset(MASTER_MATERIAL_PATH)
    if master is None:
        log_err(f"找不到母材质: {MASTER_MATERIAL_PATH}")
        log_err("请确认母材质已移动到插件目录: "
                "Plugins/WWQY_SendSetting/Content/Materials/Ma_Valorant_Master")
        return
    log(f"母材质: {MASTER_MATERIAL_PATH}")

    log(f"扫描: {SCAN_ROOT}")
    mats_data = get_assets_by_class(SCAN_ROOT, "Material")
    texs_data = get_assets_by_class(SCAN_ROOT, "Texture2D")
    sm_data = get_assets_by_class(SCAN_ROOT, "StaticMesh")
    sk_data = get_assets_by_class(SCAN_ROOT, "SkeletalMesh")

    log(f"  Material: {len(mats_data)}")
    log(f"  Texture2D: {len(texs_data)}")
    log(f"  StaticMesh: {len(sm_data)}")
    log(f"  SkeletalMesh: {len(sk_data)}")

    if not mats_data:
        log_warn("没有找到材质，退出")
        return

    # 步骤 1：修正贴图压缩
    fix_all_texture_compression(texs_data)

    # 步骤 2：贴图索引
    texture_index = build_texture_index(texs_data)
    log("")
    log(f"贴图索引（{len(texture_index)} 项）")

    # 步骤 3：创建 MI
    log("")
    log("── 创建 Material Instance ──")

    mat_map = {}
    mi_new = 0
    mi_updated = 0
    mi_unchanged = 0
    mi_skipped = 0

    for mat_data in mats_data:
        mat_name = str(mat_data.asset_name)
        mat_path = str(mat_data.package_name)
        old_mat = unreal.EditorAssetLibrary.load_asset(mat_path)
        if old_mat is None:
            continue
        try:
            old_full_path = old_mat.get_path_name()
        except Exception:
            continue

        # 传入 mat_path 以判断是否是母材质
        if should_skip(mat_name, mat_path):
            mi_skipped += 1
            continue

        base_name = strip_suffix(mat_name)
        dest_dir = "/".join(mat_path.split("/")[:-1])

        mi, is_new = get_or_create_mi(base_name, master, dest_dir)
        if mi is None:
            continue

        set_count = 0
        skip_count = 0
        miss_count = 0

        for suffix, param_name in TEXTURE_PARAM_MAP.items():
            tex = find_texture(base_name, suffix, texture_index)
            if tex is None:
                miss_count += 1
                continue
            result = set_texture_param_if_needed(mi, param_name, tex)
            if result == "set":
                set_count += 1
            elif result == "skip":
                skip_count += 1

        need_save = is_new or (set_count > 0)
        if need_save:
            try:
                unreal.EditorAssetLibrary.save_loaded_asset(mi, False)
            except Exception as e:
                log_warn(f"    保存 MI 失败: {e}")

        mat_map[old_full_path] = mi

        # is_new 为 True 也包含"父材质被更新"的情况
        if is_new and set_count == 0 and skip_count > 0:
            status = "父材质更新"
            mi_updated += 1
        elif is_new:
            mi_new += 1
            status = "新建"
        elif set_count > 0:
            mi_updated += 1
            status = "更新"
        else:
            mi_unchanged += 1
            status = "跳过"

        detail = []
        if set_count > 0:
            detail.append(f"改{set_count}")
        if skip_count > 0:
            detail.append(f"过{skip_count}")
        if miss_count > 0:
            detail.append(f"缺{miss_count}")
        detail_str = f"  ({', '.join(detail)})" if detail else ""

        log(f"  [{status}] {mat_name} → MI_{base_name}{detail_str}")

    log("")
    log(f"  MI 统计: 新建 {mi_new}，更新 {mi_updated}，跳过 {mi_unchanged}，"
        f"特殊材质 {mi_skipped}")

    # 步骤 4：替换 Mesh 材质
    log("")
    log("── 替换 Mesh 材质引用 ──")

    total_changed = 0
    total_skipped = 0
    total_meshes = 0

    for data in sm_data:
        mesh = unreal.EditorAssetLibrary.load_asset(str(data.package_name))
        if mesh is None:
            continue
        try:
            n, s = replace_materials_in_mesh(mesh, mat_map, True)
            if n > 0:
                unreal.EditorAssetLibrary.save_loaded_asset(mesh, False)
                total_meshes += 1
            total_changed += n
            total_skipped += s
        except Exception as e:
            log_warn(f"  [SM] {mesh.get_name()} 失败: {e}")

    for data in sk_data:
        mesh = unreal.EditorAssetLibrary.load_asset(str(data.package_name))
        if mesh is None:
            continue
        try:
            n, s = replace_materials_in_mesh(mesh, mat_map, False)
            if n > 0:
                unreal.EditorAssetLibrary.save_loaded_asset(mesh, False)
                total_meshes += 1
            total_changed += n
            total_skipped += s
        except Exception as e:
            log_warn(f"  [SK] {mesh.get_name()} 失败: {e}")

    log(f"  共修改 {total_meshes} 个 Mesh，替换 {total_changed} 个槽，"
        f"跳过 {total_skipped} 个槽")

    log("")
    log("=" * 60)
    log("完成")
    log("=" * 60)


if __name__ == "__main__":
    main()