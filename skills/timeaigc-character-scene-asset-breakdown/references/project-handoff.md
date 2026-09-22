# 项目资产交接 v1

## 固定入口，跨对话读取

项目根目录的 `.timeaigc/project.json` 是可读写的路径索引；`影像项目索引.md` 是给用户和新对话的简短导航。文件是事实来源，对话记忆不是。开始前从当前目录向上定位索引，定位到两个不同项目时按用户指定项目选择，不能混用。

已有约定目录优先复用。首次保存本项目资产交付时，建立轻量索引，不因此建模；制作根目录用下一个未占用的两位数字编号，如 `20_影像制作/`，不得重编号旧目录。源文件已在项目内可原位引用，项目外素材复制进 inputs，保留原件；索引内路径统一为项目根目录相对路径和 `/` 分隔符，以便整体移动/同步。

索引至少保存 `schema_version:1`、`production_root`、`documents`、`scenes`、`previews`。documents键可为script、asset_prompts、segments、storyboards，值为实际文件路径列表；不存在的不填。场景未建模时也能登记名称、ID、主图或提示词；`active_version` 为null，不伪造模型。

场景条目示例结构：`scenes.SC001 = {name, state, active_version, versions}`，versions为版本名到场景清单相对路径的映射。previews为片段ID到预演记录路径列表的映射；保留旧版本。已有场景ID和状态名原样延用，不按出场顺序重新编号。

为项目提供最小入口导航：“涉及本项目资产或镜头预演时，先读取 .timeaigc/project.json 和影像项目索引.md，按文件中的版本及确认状态继续。”已有AGENTS.md时只追加有边界的导航块，不覆盖用户规则；没有写入项目规则的授权时，把导航说明留在索引并在交付中给入口链接，不擅自修改。不能依赖其他分镜Skill自动写该索引；首次接入已有分镜时登记用户实际指定的文件。

## 目录约定

```text
NN_影像制作/
  inputs/                         # 项目外资料的本地副本
  scenes/SC001/v001/
    scene-manifest.json           # 下述场景清单
    场景档案.md                    # 来源、观察、推断、确认依据
    source/main.png               # 原主图，保留实际扩展名
    source/原提示词.md
    scene-spec.json
    model/scene.blend
    model/floorplan.svg
    model/views/V01.png ... V04.png
    model/four-views.png
    appearance/V01.png ... V04.png # V01原主图；未生成的文件不创建
    appearance/four-views.png
    prompts/补充视角.md
  previews/P006/v001/              # 由动态预演Skill创建，绑定场景版本
```

名称可适配已有项目，但登记后不得随意移动。状态变体使用独立稳定ID或明确variant键，不能把昼夜/破坏状态混成一个当前图。新版本不覆盖旧版本的源图、模型或结果。

## scene-manifest.json

必填 `schema_version:1, kind:"scene", scene_id, version, name, state, geometry_status, appearance_status, review, files, views`。

- geometry_status：`not_built / draft / ready / needs_review`；appearance_status：`not_generated / partial / complete / needs_review`，独立判断。
- review：`{by:"user|agent|unreviewed", basis:"实际确认或自审依据"}`；ready要求已审查及依据，不伪造用户确认。
- files：键为script、source_prompt、main_image、scene_spec、blend、floorplan、dossier、view_prompts、gray_grid、appearance_grid；值为 `{path, sha256}`，仅登记存在文件。路径相对项目根目录。普通脚本/提示词的当前文件也保留哈希，变动即提示复核，不静默使用旧摘要。
- views：四项，ID固定V01–V04；包含label、camera_id、gray（文件引用）、appearance（文件引用或null）。V01标记 `main_view:true`；其他false。V01 appearance必须对应未重生成的主图。scene-spec中存储所有相机参数。
- geometry_status为ready时要求blend、scene_spec、floorplan和四张gray真实存在；appearance_status为complete时要求main_image、四张appearance和成品拼图存在，且V01与main_image哈希相同。缺图不伪造complete。
- 档案记录可观察事实、推定尺寸、不可见区域补全、入口与动线、关键物件、单位/原点/方向和V01匹配误差；不要求用户阅读机器字段才能做选择。

`active_version` 只指向明确采用的几何ready版本；草稿可以登记但不自动替换已采用版本。新版本确认后保留旧清单，下游比较绑定版本和内容哈希，旧预演标需复核，不删除历史视频。

## 写入、核验与恢复

生成文件后计算SHA256并写清单；所有JSON/MD显式UTF-8。先写版本文件，校验后更新索引，再更新用户导航。使用临时文件原子替换索引，提交前重新读取并合并其他对话已写入的条目；发现同时修改冲突时保留双方版本后处理，不整份覆盖。新对话不凭文件修改时间或最大的版本号擅自选“最新”。

运行 `python scripts/validate_handoff.py --root <项目根目录> --manifest <scene-manifest.json>`。校验器检查路径、文件指纹和声明完整性，不判断图像空间质量；仍需目视和Blender检查。

验证成功后将清单相对路径加入project.json，给用户主图、四视图、模型和索引的实际链接。仅常规提示词路径不用伪建scene-manifest；索引中保留documents及无模型场景条目即可。
