# ValorantConvert
Valorant 枪皮/挂饰模型转换工具链 —— Blender 建模导出 ↔ UE 材质自动配置 + 预览地图生成。

## 结构

```
├── ue/Plugins/WWQY_SendSetting/     UE 插件（Python 脚本 + 母材质 + BP 蓝图）
├── blender/valorant_psk_converter.py  Blender 插件（PSK 导入 → 处理 → 导出）
├── docs/                              交接文档 / 协作规约 / 历史 patch
└── .gitignore
```

## 工作流

```
Blender 导出 → /Game/Gun/{skin}/  (UE)
│
▼
auto_material_watcher 监听资产变化（防抖 3s）
│
▼
batch_material_setup 修贴图压缩 + 建 MI_ + 替换材质槽
│
▼
（自动预览地图开启时）防抖 5s → create_overview 生成预览关卡
```

## 命名约定

| 类型 | 格式 |
|---|---|
| 枪身 | `EQ_{gun_type}_..._Skelmesh` / `GN_{gun_type}_..._Skelmesh` |
| 配件（复制后） | `GN_{gun_type}_{RedDot\|Silencer}_Skelmesh` |
| 弹匣 | `GN_{gun_type}_Magazine_...` |

`gun_type` 枚举：`BasePistol` / `AutoShotgun` / `Melee` / `Burst` / `Carbine`

## 文档

- `docs/交接文档.md` —— 项目全貌、契约、待办、历史决策
- `docs/协作规约.md` —— 协作流程
- `docs/*.patch` —— 历史改动基线

## 依赖

- Blender 4.2+（`io_scene_psk_psa`、`send2ue` 插件由主插件自动安装）
- UE 5.8+（Python Editor Script Plugin、Editor Scripting Utilities）
