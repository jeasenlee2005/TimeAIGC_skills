# 项目级角色尺度与简模登记

本文件是 v2.2 前置确认规则与脚本契约。人类简模是可复现空间代理，不是医学人体标准；四足和其他形状代理也是占位几何，不是动物解剖或真实表演。

## 先确认完整来源，再问缺项

使用 `project_preflight.py` 前先读索引登记的真实文档。优先级为：有效且已确认的第一阶段全项目片段总表 `segments` → 完整剧本 `screenplay`。仅当前镜头、节选或未确认总表不满足第一项。完整来源均缺失时，只问“请提供已确认的全项目完整片段总表，或完整剧本；收到后再统一确认角色尺度与简模”，并等待，不先猜角色或批量问臆造人物的身高。

已确认完整片段总表优先于剧本；无合格总表可用时使用完整剧本。记录实际采用来源及内容哈希，不能仅凭文件名中的“完整版”判断完整。完整角色清点需要阅读和明确依据：程序并不从自然语言自动发现全部人物。

来源存在后，核对全项目角色/形态登记并一次列齐缺项。有效确认直接复用；新角色、未登记别名、新形态、缺尺寸、单位歧义或实际模型冲突才问。参考图换版或文件夹迁移不抹掉已有尺寸。可以用 [批量确认模板](../assets/templates/character-confirmation-request.md) 组织人类可读的问题，不把技术 JSON 交给用户填写。

## 文件与身份

项目 `.timeaigc/project.json` 保留原 schema 与其他字段，增加：

```json
{
  "project_id": "project_example",
  "character_registry": {"path": ".timeaigc/character-registry.json"},
  "active_scene_id": "scene_main"
}
```

这只是合并字段示意，不是一份可以覆盖原索引的完整文件。登记文件采用 `schema:"timeaigc-character-registry/v1"`，其 `project_id` 必须对应项目。主要字段：

| 字段 | 保存内容 |
| --- | --- |
| `authoritative_sources.segments / screenplay` | 项目相对路径、真实 `sha256`、`scope:"complete_project"`、状态与 `confirmation_source` |
| `cast_inventory` | 采用来源 `source_sha256`、`status:"confirmed"`、`confirmation_source`、完整 `character_ids` |
| `characters.<character_id>` | `name`、`aliases`、固定 `color` RGB、`color_name`、`forms` |
| `forms.<form_id>` | `kind`、`confirmation`、`measurements`、`model`、可选 `protrusions` |
| `scene_units.<scene_id>` | 真实单位换算、确认来源与已知尺寸校准对象 |

`segments.status` 必须为 `confirmed`；完整剧本可为 `provided` 或 `confirmed`，但全项目角色清点仍需确认依据。普通确认对象为 `{status:"confirmed",source:"实际确认来源"}`。来源应能说明是谁、基于什么确认；不能填“用户已确认”来代替没有发生的回答。合成演示使用明确的 fixture 来源，真实项目必须替换为自己的事实。

稳定 ID 不随显示名、图名或文件夹变化。别名只解析身份，冲突别名应问清，不能随机选人。一个角色的常态、巨型化、变兽等分别使用形态 ID，并分别确认尺寸；新形态不能覆盖原形态。颜色在角色级保存，供实际画面和生成提示词一致映射。

## 尺寸与模型选择

每个尺寸均为 `{value,unit,semantic,confirmation}`；单位支持 `m/cm/mm`，计算统一为米，正数须有限。角、耳、尾的附加长度可为 0，表达明确无突出量；未知不是 0。

| 类型/字段 | 测量含义 | `semantic` |
| --- | --- | --- |
| 人类 `height` | 脚底至头顶总高，包含头 | `sole_to_crown_including_head` |
| 四足 `shoulder_height` | 地面至肩峰 | `ground_to_withers` |
| 四足 `head_top_height` | 地面至裸头顶部，不含角耳 | `ground_to_head_top_excluding_protrusions` |
| 其他 `body_height` | 主体垂直范围，不含突出物 | `body_vertical_extent_excluding_protrusions` |
| `body_length` | 身体前后长度，不含尾；当前四足几何的头部另放在主体前方 | `body_fore_aft_extent_excluding_tail` |
| `body_width` | 身体横向宽度，不含突出物 | `body_lateral_extent_excluding_protrusions` |
| `horn_height / ear_height` | 从裸头顶部向上的突出高度 | `horn_vertical_extent_above_head_top / ear_vertical_extent_above_head_top` |
| `tail_length` | 超出身体后端的尾部延伸长度 | `tail_extension_beyond_body_rear` |

人类必填 `height`；四足必填肩高、头顶高、体长、体宽；其他形态必填主体高、长、宽。对剧情重要的角、耳、尾必须进一步问清，不将外轮廓塞进“身高”。不能从绘图像素或透视截图猜现实尺度后标记确认。

非人类先明确三选一：

| `model.choice` | v2.2 行为 |
| --- | --- |
| `existing_model` | 只读追加已登记本地 `.blend` 的精确静态集合；保留网格、拓扑、实体材质及部件变换。必须保存 `path/sha256/version/collection` 与 `asset_contract:{path,sha256}`，并通过 Blender 实测 |
| `simple_proxy` | 用已确认尺寸生成固定形状代理；四足为接地身体体块加头，不生成四肢解剖或步态 |
| `placeholder` | 用已确认尺寸生成明确的占位几何；保留该选择，不能称为已完成模型 |

`existing_model` 的准备、实测契约与登记命令见 [本地模型复用](local-blend-models.md)。该路线仅支持符合契约的静态 `MESH/EMPTY` 集合；不是任意格式或带绑定/动画/外部依赖的通用导入。路径丢失或模型字节不符报 `model_file_conflict`，契约问题报 `existing_asset_contract_conflict`；实际集合、网格、尺度等仍须 Blender 再验。保留已确认的尺寸和选择，只解决对应问题，不静默退回占位体。

普通人类 `model.choice:"cylinder"`，以固定躯干/头比例按确认总高缩放。实现支持 0.05–100m 的工程占位范围，以包含巨型角色；它不是推荐的真实人体范围。未提供身高时正式项目不使用 1.76m 示例默认值。特殊手部代理沿用独立明确选择流程，非普通项目登记的默认圆柱，不是 IK 或接触验证。

所有模型选择都必须有 `model.choice/version/path/sha256`。生成器模型的 `path` 指向项目内已保存的定义 profile JSON，`sha256` 是该文件真实哈希；已有模型的路径指向实际 `.blend`，`collection` 为必填精确集合，`asset_contract` 指向已实测的 JSON 及其字节哈希。运行后另记录预演 `.blend` 与实际几何摘要。不要填假路径或假哈希；同内容文件仅改路径可重连，模型定义/文件字节、集合、契约或版本变了必须复核。

`simple_proxy/placeholder` 的非规则突出物可用 `protrusions:[{id,kind,bounds_min_m:[x,y,z],bounds_max_m:[x,y,z],confirmation}]` 分开登记。坐标以角色局部地面根点为原点、Z 向上、正面 -Y；边界单位已是米，不随镜头位置变化。其几何是包围体，不能伪称真实角耳尾形状。相同突出物不要同时用标量和独立包围体重复生成。`existing_model` 不接受这类额外代理包围体，须以契约中的真实网格测量规则表达已登记的突出物尺寸。

## 片段引用与固有尺寸分离

`--clip-actors` 指向 JSON 数组，例如：

```json
[
  {
    "id": "actor_hero",
    "character_id": "hero",
    "form_id": "base",
    "keys": [
      {"time": 0, "position": [0, 0, 0], "yaw_degrees": 0},
      {"time": 3, "position": [1, 0, 0], "yaw_degrees": 45}
    ]
  }
]
```

也可用已登记的 `character_alias` 解析人物，但必须明确 `form_id`。这里只列片段需要的演员实例；同一角色不同实例有各自 `id`。不能在片段内传尺寸、颜色、模型、形态几何或缩放字段覆盖登记；包括 `height/height_m`、`measurements/measurements_m`、`color/color_name`、`model` 及 `model_*`、`proxy_mode/hand_lod`、`scale/body_scale/form_scale`、`shape_kind/protrusions`。位置和相机输入使用米；进入场景时转换一次。当前标准支持根位置/整体朝向，不因字段名“pose”就具备坐卧、四肢或关节能力。

程序返回 `resolved_actors` 和 `character_binding`，只有本片使用的角色被构建。全登记保存在快照中，绑定的 `used_forms` 仅包含实际使用形态的指纹。

## 实际场景单位与校准

例：米场景每 Blender 单位为 1m，厘米场景每单位为 0.01m：

```json
{
  "status": "confirmed",
  "meters_per_blender_unit": 0.01,
  "source": "项目实际尺度确认依据",
  "calibration": {
    "object": "CalibrationCube",
    "axis": "x",
    "length_m": 2,
    "tolerance_m": 0.00001
  }
}
```

在厘米场景中，上例对象 X 世界范围应为 200 Blender 单位，即 2m。校准读取真实网格经世界变换后的范围，同时核对场景单位设置。只把设置改成“厘米”而几何仍长 2 单位会失败。人物几何在固有米制下构建，根尺度乘 `1/meters_per_blender_unit` 一次；重复适配拒绝。不能先把坐标除 0.01，又对几何重复除一次。

纯 Python 预检的 `ready` 仅表示资料可进入 Blender，报告中的 `blender_calibration_pending:true` 不是实测通过。必须再运行真实 Blender 校准和渲染。未知尺度先用 [尺度确认模板](../assets/templates/scene-scale-confirmation.md) 问清，源场景只读。

## 命令与过期规则

```text
python <skill>/scripts/project_preflight.py --project-root <project-root> --scene-id <scene-id> --clip-actors <actor-references.json> --output <new-preflight-report.json>
```

退出 0 为资料预检 ready；退出 2 为需输入或数据错误。脚本输出结构化问题，不会替用户作确认，也不会自行在终端等待回答；调用它的助手负责展示缺项并等待。没有完整来源时，先输出材料请求，不混入推测人物问题。

明确修订登记后，可运行 `--mark-stale` 追加项目索引中的受影响预演过期事件；不重写旧预演和媒体。代码 API `update_confirmed_form(project_root, character_id, form_id, new_form, reason=...)` 接收已取得真实确认的新形态记录，在 `revision_history` 保存旧记录和语义哈希，再标记依赖结果；它本身不替用户确认。`register_preview(project_root, manifest_path)` 将验证后的 schema3 预演追加到项目索引，保留已有说明和条目，拒绝重复路径。语义指纹包括尺寸、尺寸含义、突出物、颜色、模型选择/版本/哈希，排除别名、参考图和路径。未受影响角色的更新不使本片过期。新源文档或场景校准变化同样需要重新核对。

`schema_version:3` 参考清单携带 `registry-binding.json`、`registry-snapshot.json` 与 `project-index-snapshot.json`；绑定记录两个快照的相对路径与实际哈希。校验器重新核算冻结快照，并对照当前项目来源、使用形态、模型文件和尺度；快照不能代替当前性检查。项目整体迁移可保持相对路径和确认尺寸，输出在项目外时传 `--project-root`。旧 v2.1 schema2 可按原规则检查；不能删除 schema3 绑定或降级声明绕过角色检查。

完整可运行实例见交付根 `demo-project/`，其中来源、身高、模型选择和尺度均是合成测试数据。复制到正式项目时只借结构，不能复制测试确认结论。
