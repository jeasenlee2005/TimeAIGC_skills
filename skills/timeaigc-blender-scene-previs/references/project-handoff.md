# 读取场景资产，保存动态预演

## 新对话的入口

从用户指定项目或当前目录向上查 `.timeaigc/project.json` 与 `影像项目索引.md`。JSON是路径/版本事实来源；不同对话不共享可靠记忆，也不能把同名项目当作已同步文件。只能访问当前实际文件，找不到时索取索引或场景交接包，不让用户重复粘贴已在项目中的剧本和提示词。

项目索引schema_version为1，含production_root、documents、scenes、previews。路径相对项目根目录，使用`/`；不依赖用户机器盘符。documents可登记script、asset_prompts、segments、storyboards路径列表。通过用户片段ID找实际定稿，不以最新修改时间擅自猜版本。

scenes以稳定场景ID为键，每项含name、state、active_version和versions；versions映射版本名到scene-manifest.json路径。active_version为空意味着未选择可用模型，不自动建模；多个状态/版本有歧义时只确认该项。用户直接指定旧版时记录其选择并先复核，不静默切回当前版。

## 场景包检查

scene-manifest.json为`{schema_version:1,kind:"scene",scene_id,version,name,state,geometry_status,appearance_status,review,files,views}`。files的每个值都是`{path,sha256}`，应含blend、scene_spec、floorplan以及实际已完成的主图、提示词等；views为V01–V04，包含camera_id、gray引用、appearance引用或null，V01为main_view。

geometry_status必须ready，review必须有实际审查依据。appearance_status为partial不阻止本地预演，但必须明确生成素材尚缺；不能假装成品四视图已经完成。匹配剧本状态、米制坐标、原点、人物预期身高、相机传感器和空间边界。查看实际模型与必要图片，不仅信任清单。

运行 `python scripts/validate_handoff.py --root <项目根> --manifest <scene-manifest.json>`，检查真实文件和指纹。不一致先定位源改动并复核，不能直接刷新哈希“让校验通过”。本地检查不证明外部视频生成准确。

没有规范清单但用户有旧.blend时，不重建：检查无人模型、单位、布局和实际文件，按同一schema建立最小交接清单并登记；主图/四视图缺失不伪造complete。模型本身缺失、需要补空间或重做美术时，交回角色场景资产Skill处理，明确已有输入及缺项；未安装该Skill不假装调用。

## 创建预演版本

在索引production_root下创建 `previews/<片段ID>/vNNN/`，源场景只读。保留源分镜原文和哈希、原片段时间范围、场景版本及场景清单哈希。全片局部时间0对应原source_range起点；镜头数与一镜到底属性沿用定稿。

写入preview-manifest.json：

- `schema_version:1, kind:"previs", clip_id, version, scene_id, scene_version`。
- `scene_manifest:{path,sha256}` 与 `storyboard:{path,sha256}`；均项目根相对路径。
- `source_range:[原起点,原终点]`、duration、shot_count、one_take（布尔）；一镜到底shot_count为1，不能为适配平台改切镜。
- `status:"draft|ready|needs_review"`、`review:{by:"user|agent|unreviewed",basis}`。
- `files`：blend、video、prompt、upload_list及可选mapping，每项`{path,sha256}`。ready要求前四项齐全且已实际审查。
- 上传清单逐项列素材编号、路径、人物/场景对应、职责、时间范围；编号与完整提示词一致。未生成视频时不填任务成功。

保存原分镜、机位/整体走位映射，导出干净MP4和内部审查图。完整生成提示词保留原标题、时长、影调、角色、场景、全部镜头、表演、对白和声音；空间修订需要可追溯，不能删剧情换成“照视频动”。默认不附额外时间参考图。

验证preview-manifest后，将相对路径追加至project.json的previews条目，再更新影像项目索引.md。索引更新前重读、合并、原子替换，保留其他对话的已有条目；并发冲突不得整份覆盖。源场景/分镜内容或active_version改变会触发重新审查，旧预演保留历史身份，不自动用于新版生成。

校验器对active_version变化采用保守拒绝；用户明确选择旧版时，可在新预演记录增加historical_scene_review:{by:"user",basis:"用户选择与复核依据"}，允许固定旧版但仍检查所有原文件哈希。不得伪造用户选择、改主项目采用版本或篡改旧哈希来绕过检查。

## 用户交付

默认给预演视频、完整提示词、素材清单以及简短通过/偏差说明，附索引入口。将模型和资产查找藏在流程内部，不要求用户记固定调用话术或填写技术字段。用户自行生成时停在材料交付，不擅自提交付费任务。
