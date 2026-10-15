# 复用已登记的本地静态 .blend 角色

用于非人角色已经有合适的本地 `.blend`，或先前建模后希望以后新任务继续沿用的情况。选择 `existing_model` 会从实际资产追加指定集合的网格，并保留其拓扑、材质和部件变换；不会调用体块生成器替换模型。`simple_proxy` 和 `placeholder` 则按登记参数重新生成几何，两者不能混称“加载原模型”。

本流程只处理已经准备成以下契约的静态本地 `.blend`。它不是任意格式转换、骨骼绑定、自动修复或通用模型清理工具。遇到不支持的输入保留已有尺寸和模型选择，报告具体对象/字段，先完成独立资产适配；不得悄悄改用体块或反复询问有效身高。

## 资产准备约定

保留原资产，必要的整理写入新的角色资产版本。后续检查和渲染只读资产文件，输出到新预演目录。

- 一个明确命名的集合，只含 `MESH` 与 `EMPTY`；每个对象有非空、唯一且稳定的字符串自定义属性 `timeaigc_asset_id`。
- 集合有数值属性 `timeaigc_meters_per_unit`，与契约 `meters_per_unit` 一致。它说明资产每本地单位对应多少米；不能只改标签来假装几何已校准。真正放行还需下面的实际顶点尺寸与确认尺寸吻合。
- 契约 `root_object` 指定一个根 Empty 的稳定 ID。根的局部原点表示地面，局部 `+Z` 向上；正面在根局部水平轴 `+X/-X/+Y/-Y` 中明确选择。所有其他对象必须在根内，父子依赖不得逃出集合。
- 头、身体、接地部分、正面标识及用于身份颜色的网格均在契约中明确选择。正面标识实际应在头与身体前方且高于地面，不只依赖名字或一个不可见 Empty。
- 材质保留源数据：支持无节点实体材质，以及严格的常量 `Principled BSDF → Material Output.Surface` 两节点、单连线材质。后者不允许 Principled 输入连线、动画/驱动、禁用节点或非激活输出，Alpha 必须为 1、Transmission Weight 必须为 0；所有着色器常量与输出目标都进入身份摘要。契约 `color_objects` 的实际使用材质 `diffuse_color` RGB 须符合登记颜色，这是本管线 Workbench 的颜色依据；不要求它等于源着色器 Base Color，也不会自动重染色或转换材质。
- 拒绝嵌套集合、集合外父级/依赖、链接库、集合实例、骨架及其他对象类型、修改器、约束、动画/驱动、形态键、顶点组、自定义法线、UV/顶点色/自定义网格属性以及超出上述常量两节点结构的材质。隐藏对象/集合、透明/透射/背面剔除材质、对象级材质覆盖、反射/塌缩/剪切变换也不支持。不得用静默烘焙、删除功能或替换材质来假称原模型已无损复用。

在 Blender 中整理副本时可以通过对象自定义属性填写稳定 ID，通过集合自定义属性填写单位；保存 `.blend` 后，再检查文件字节与实际几何。加载检查使用 `--disable-autoexec` 和库数据追加路径，不以模型内 Text 脚本、处理器或驱动执行资产逻辑。不要为了“让模型工作”启用自动执行。

## 契约：身份与实际尺寸分开

`scripts/blend_asset_contract.py` 定义 `schema:"timeaigc-blend-asset/v1"`。先准备草稿 JSON；唯一可缺省的最终身份字段是 `expected_geometry_sha256`，由真实 Blender 检查生成。不得自己编一个摘要来代替检查。

| 字段 | 含义 |
| --- | --- |
| `collection` | `.blend` 内精确集合名称，与登记 `model.collection` 相同 |
| `root_object` | 根 Empty 的 `timeaigc_asset_id`，不是可随意变动的显示名 |
| `meters_per_unit` | 源资产本地单位到米的比例，与集合属性相符 |
| `forward_axis / up_axis` | 根局部正面轴与 `+Z`；只接受上述水平正面轴 |
| `tolerance_m` | 正数且不超过 0.001 米的尺寸/接地容差 |
| `shape_kind` | `quadruped` 或 `other`，与登记形态一致 |
| `ground_objects / body_objects / head_objects / front_marker_objects / color_objects` | 各自用途的非空稳定网格 ID 列表 |
| `measurement_rules` | 每项登记尺寸对应的完整网格对象选择、规范坐标轴及固定语义运算 |
| `expected_geometry_sha256` | 实际检查所得网格拓扑、材质及规范变换身份摘要 |

规则在**规范米制坐标**中测量：局部 `-Y` 为正面、`+Z` 向上。`extent` 为选中网格总范围，`max` 为相对地面根点的最大坐标。当前尺寸语义限定如下，不能自由改轴或模式来凑合格数值：

| 尺寸键 | `axis / mode` | 实际选择要求 |
| --- | --- | --- |
| `body_width` | `x / extent` | 恰好选择 `body_objects` |
| `body_length` | `y / extent` | 恰好选择 `body_objects` |
| `body_height` | `z / extent` | 恰好选择 `body_objects` |
| `shoulder_height` | `z / max` | 恰好选择 `body_objects`，真实体块上界须能表达肩高 |
| `head_top_height` | `z / max` | 恰好选择 `head_objects`，不包含角耳 |
| `horn_height / ear_height` | `z / extent` | 所选突出部分最低 Z 必须从裸头最高 Z 开始，在容差内相接 |
| `tail_length` | `y / extent` | 所选尾部最小 Y 必须从身体最大 Y 开始，在容差内相接 |

尺寸规则键必须与登记的 `measurements` 键完全相同，包括登记过的角耳尾尺寸。选择粒度是整个网格对象，不会自动识别或切分连成一体的头、身、角、尾；也不会将嵌入头/身内部的附肢根部错误计入突出长度。复杂相对测量、弯曲附肢或单网格部件不能准确表达确认语义时，应明确停止并在独立资产版本适配，不靠改登记含义通过。

非规则 `protrusions` 代理包围体不叠加到已有网格：该路线要求通过真实部件测量突出物，防止追加并不存在的体块。`simple_proxy/placeholder` 的独立包络规则仍按原流程执行。

导入器去掉源根的刚性平移与旋转，保留其实际尺度，依据声明的源单位与正面轴规范化，再且仅再做一次目标场景单位换算。不根据期望身高拉伸或缩放到合格；实测尺寸不符就停止。接地部分最低点与全模型最低点都须落在根地面附近，不能有偷偷埋地的网格。

原 Empty 层级的静态变换会合并到网格的规范变换，网格挂到新的演员根节点，显示名也会带本次演员实例 ID。因此“保留原模型”指保留网格数据、拓扑、实体材质与相对几何；不承诺保留源 Empty 层级、显示名或 `.blend` 文件字节。源文件本身不保存、不改变。

## 可执行检查与契约生成

以下由助手在用户批准的项目资产目录操作，路径占位符替换为实际路径。先读取现有登记和实际模型，按真实部件准备 [契约草稿示例](../assets/templates/local-blend-descriptor.example.json)；示例对象 ID 只是结构示意，不能当作真实模型的部件。读取实际资产时也须禁用自动执行。

另保存 `measurements-m.json`，它是从已确认登记换算出的纯数值字典，例如四足形态为 `{"shoulder_height":0.9,"head_top_height":1.4,"body_length":1.8,"body_width":0.6}`。这些示例数字不能覆盖项目的真实确认。全部单位已是米，键与登记完全一致；`--color` 使用登记的 RGB 值。

```text
blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python <skill>/scripts/inspect_blend_asset.py -- --asset <project>/assets/characters/beast/v002/beast.blend --descriptor <project>/assets/characters/beast/v002/descriptor-draft.json --measurements <project>/assets/characters/beast/v002/measurements-m.json --color 0.15 0.72 0.3 --output <project>/assets/characters/beast/v002/asset-contract.json --geometry-output <project>/assets/characters/beast/v002/asset-geometry.json
```

`--output` 必填，`--geometry-output` 可选；二者须为不同的**新文件**，不能等于输入或覆盖已有报告。成功输出 `status:"verified"` 及几何摘要，最终契约包含 `expected_geometry_sha256`。检查实际网格时同时验证全部确认尺寸、地面、前向标识和颜色；未通过不会生成一份可绕过该问题的契约。再次检查已有最终契约时，已存几何摘要也是必须匹配的断言，不能自动刷新。

该命令只读取资产、生成契约/报告，不修改模型、不登记角色，也不替用户做尺寸确认。它不会自动给任意模型补根节点、改 ID、修单位、烘焙绑定或生成头/正面标识。若现有资产尚不满足准备约定，先在独立新资产版本明确适配并检查，不改原模型。

## 将已验证模型写入现有形态

在保留真实确认来源的前提下，使用 `character_registry.update_confirmed_form` 更新已有角色的形态，保留完整旧记录并标记相关预演过期。下面是供助手保存为一次性本地脚本的 API 示例；实际角色/形态 ID、路径、版本与修订依据应来自当前项目，不让用户填写技术 JSON：

```python
import copy
import hashlib
import json
import sys
from pathlib import Path

skill = Path(r"<skill>")
project = Path(r"<project>")
sys.path.insert(0, str(skill / "scripts"))
from character_registry import update_confirmed_form

index = json.loads((project / ".timeaigc/project.json").read_text(encoding="utf-8-sig"))
registry = json.loads((project / index["character_registry"]["path"]).read_text(encoding="utf-8-sig"))
character_id, form_id = "beast", "base"  # 使用当前项目真实 ID
form = copy.deepcopy(registry["characters"][character_id]["forms"][form_id])
model_path = "assets/characters/beast/v002/beast.blend"
contract_path = "assets/characters/beast/v002/asset-contract.json"
contract = json.loads((project / contract_path).read_text(encoding="utf-8-sig"))
sha256 = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
form["model"] = {
    "choice": "existing_model",
    "path": model_path,
    "sha256": sha256(project / model_path),
    "version": "v002",
    "collection": contract["collection"],
    "asset_contract": {"path": contract_path, "sha256": sha256(project / contract_path)},
}
result = update_confirmed_form(
    project, character_id, form_id, form,
    reason="填写本次已获批准的模型沿用依据或确认记录位置",
)
print(json.dumps(result, ensure_ascii=False, indent=2))
```

此示例保留原尺寸和确认；不自动删掉与已有网格不兼容的 `protrusions` 代理盒。存在这类记录时，应先明确将其语义落实为真实模型的测量规则，并经确认更新完整形态，不能为通过检查直接清空。新增角色/形态还需按 [角色登记](character-registry.md) 补全全项目清点及身份，不把这个更新函数当作自动发现人物的工具。

登记后运行 `project_preflight.py`，再按 [本地执行](execution.md) 渲染当前片段。预检成功仅说明文件与登记可用，真正的网格加载和尺度复验发生在 Blender 中；不要以 `ready` 代替导入或实渲成功。

## 登记后，新任务如何找到原模型

模型登记放在项目角色/形态记录中，保留 `choice:"existing_model"`、项目相对 `path`、实际 `.blend` 文件 `sha256`、`version`、精确 `collection`，并增加 `asset_contract:{path,sha256}`。契约文件路径也相对项目根，哈希为最终 JSON 字节的实际 SHA256。辅助几何检查报告可随资产版本保存，但不能代替契约及文件哈希。

新任务先从项目 `AGENTS.md` 与 `.timeaigc/project.json` 读取 `character_registry.path`，再加载这个形态的模型和契约。无需旧聊天或用户重新贴模型路径。已有有效的尺寸、模型选择、版本及确认来源直接复用；仅缺失文件、集合错误、实际尺度/颜色冲突或内容变更需要处理对应差异。

模型字节、版本、集合、契约或固有尺寸变更后，依赖旧形态的预演必须过期；不能只刷新清单哈希使旧画面成为当前版本。同字节模型及契约改路径或项目整体迁移可以重连相对路径，保留确认尺寸和身份；重新执行预检及实际导入检查。

## 实测验收要求

以下是本路线应执行的验收，不代表文档本身已验证通过：

1. 在新 Blender 进程保存或取得非人 `.blend`，再从项目文件发现并加载它；不把先前进程中的网格对象当作加载成功。
2. 对照稳定部件 ID、网格拓扑摘要、材质与规范变换，证明直接使用了登记模型；重复加载、项目迁移及米/厘米场景应保留同一物理几何身份。
3. 保存预演后独立重开，实际渲染正侧背、头部/正面标识、颜色和接地，检查首中尾与切点；清单记录实际加载路径、资产/契约哈希、版本、集合和几何摘要。
4. 验证丢失文件、错误集合、改过网格、尺寸冲突、不支持依赖等会停止；旧参考的当前性检查也应拒绝资产/尺寸改变。
5. 双版仍共用原时间轴和帧源，干净版无文字。静态原模型只做根位移与整体朝向，不把复杂外形误写为骨骼、IK、接触或表演验证。

实际执行命令与测试结果由本轮交付报告记录；完整来源、场景校准、文字和参考保护仍遵循 [本地执行](execution.md)。
