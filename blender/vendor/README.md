# Blender 依赖插件离线兜底（vendor）

本目录为 `valorant_psk_converter.py` 的两个外部依赖提供**离线兜底**手段：
网络不可达、一键联网安装失败时，无需依赖网络即可手动安装。

## 依赖清单
| 插件 | 版本 | 授权 | 来源 |
| --- | --- | --- | --- |
| Unreal PSK/PSA（`io_scene_psk_psa`） | 9.1.3 | GPL-3.0-or-later | https://github.com/DarklightGames/io_scene_psk_psa |
| Send to Unreal（`send2ue`） | 2.6.7 | MIT（社区 fork） | https://github.com/poly-hammer/BlenderTools/releases |

> - **PSK/PSA 已内置于本仓库**：`io_scene_psk_psa_v9.1.3.zip`。
> - **Send to Unreal 不随仓库分发**，需从上方官方 Release 页自行下载 `send2ue_2.6.7.zip`。

## 网络正常时（默认行为）
`valorant_psk_converter.py` 中的「安装 PSK / 安装 Send」按钮会：
1. PSK 优先尝试 Blender 扩展平台安装；
2. 失败则回退到 GitHub 下载最新 release 并自动安装。
全程联网，无需手动介入。下载失败时界面会弹出上方离线兜底提示。

## 网络失败时（手动安装）

### PSK/PSA（仓库已内置）
1. 找到 `blender/vendor/io_scene_psk_psa_v9.1.3.zip`
2. Blender 5.x：`Edit → Preferences → Extensions → Install from Disk`，选择该 zip
3. 在扩展列表中启用（**要求 Blender ≥ 5.0.0**）

> ⚠️ 本 zip 为 Blender **扩展格式**，**仅支持 Blender ≥ 5.0.0**。
> 若你使用 Blender 4.2–4.x，请从上游按版本下载对应包：
> https://github.com/DarklightGames/io_scene_psk_psa/releases （本仓库不提供旧版）。

### Send to Unreal（需自行下载）
1. 打开 https://github.com/poly-hammer/BlenderTools/releases
2. 下载 `send2ue_2.6.7.zip`
3. Blender：`Edit → Preferences → Add-ons → Install from Disk`，选择该 zip
4. 启用 Send to Unreal

## 授权说明
- `LICENSES/io_scene_psk_psa_LICENSE.txt` 为 GPL-3.0 全文，
  取自 https://www.gnu.org/licenses/gpl-3.0.txt （SPDX: GPL-3.0-or-later）。
- Send to Unreal 属 `poly-hammer/BlenderTools` 社区 fork（**MIT**，许可证来源：该仓库 `LICENSE.md`，
  经 GitHub API 与仓库文件双重核实），与 Epic Games 无关，使用请遵守其许可证条款。
