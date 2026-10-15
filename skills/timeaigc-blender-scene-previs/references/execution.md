# v2.2 本地执行

先按 [项目接入](project-handoff.md) 和 [角色登记](character-registry.md) 完成全项目资料、角色/形态与场景尺度确认。这里的命令在独立副本运行，所有输出使用新目录；不覆盖原技能、旧输出、源场景或仓库，不安装、不推送、不操作 Library。本轮不执行付费视频生成。

## 环境

使用已有 Blender、真实 Python 解释器、Pillow、NumPy 及可读中文 TTF/TTC/OTF 字体。不要使用 WindowsApps 占位解释器。JSON、源文本和日志使用 UTF-8。

编码与完整解码由 `scripts/blender_video.py` 调用 Blender 内置 FFmpeg 完成。已有环境的外部 FFmpeg 曾返回“拒绝访问”；不要借此提权、改权限或安装来源不明软件，也不要把 Blender 编码成功说成外部 ffprobe 或服务适配器已可运行。实际软件版本以测试报告为准。

以下路径是命令参数示意，需替换为实际绝对路径；本机 Blender 和字体路径可含空格，应在 shell 中按规范引用。

## 1. 项目预检

已有非人角色模型先按 [本地模型复用](local-blend-models.md) 检查实际静态 `.blend` 集合，保存实测契约并登记。已有有效模型、契约、尺寸和确认来源直接沿用；不能因为另开任务就重新生成体块。纯 Python 预检只检查文件/登记，实际网格安全性、身份与尺度由 Blender 导入过程复核。

```text
python <skill>/scripts/project_preflight.py --project-root <project> --scene-id scene_m --clip-actors <project>/clip-actors.json --output <new-preflight-report.json>
```

`--clip-actors` 是仅含本次演员引用与根位移/朝向的 JSON 数组。脚本先检查完整来源，再批量列登记缺项，不从这些片段演员反推全项目角色。退出 2 时处理实际问题并等待必要用户输入；不要补默认身高或伪造确认。退出 0 的 `ready` 仅表示资料可进入 Blender，`blender_calibration_pending:true` 必须由下一步实测解决。

登记修订后可另运行 `--mark-stale` 更新项目索引中过期事件；旧清单、视频和快照不改。该参数会写项目索引，普通预检没有这一副作用。

## 2. 从已有无人场景渲染

正式入口必须同时给项目根、已登记无人场景和当前镜头数据：

```text
blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python <skill>/scripts/build_scene.py -- --project-root <project> --scene-blend <project>/production/scenes/scene_m/v001/scene.blend --spec <project>/inputs/scene_m-preview.json --out <project>/production/previews/meters/v005 --render clips
```

上例对应合成演示目录约定，使用新输出 v005；已交付 v004 不重跑，v001 为诊断历史。真实项目使用自己的场景/片段 ID 与生产目录。米与厘米演示分别使用 `scene_m`、`scene_cm` 及相应输入，不把同一场景仅换标签冒充另一单位。

开始渲染前，`scene_binding.py` 从项目索引的 `scenes[scene_id].active_version/versions` 找场景清单，要求清单 `id/version` 匹配、`state` 为 `confirmed` 或 `approved`，且 `files.scene:{path,sha256}` 指向实际 `.blend`。scene 路径相对清单目录；`--scene-blend` 必须是同一规范路径且实际哈希相同。未登记场景、错版本、改过字节或另传路径都会拒绝，不能靠相同单位设置代替这项绑定。

`build_scene.py` 在 Blender 中对已登记校准网格实测世界范围，乘 `meters_per_blender_unit` 后与确认米值比较，并检查场景单位一致；失败就停止。源场景不缩放、不覆盖。角色固有几何以米定义，进入场景时只做一次单位换算；位置与相机也从米转换为场景单位。重复人物适配会拒绝。

项目路径只创建本片实际出场的角色实例。普通人类由 `canonical_actor.py` 生成圆柱加头；非人 `simple_proxy/placeholder` 由 `shape_proxy.py` 按参数生成体块；`existing_model` 从登记 `.blend` 只读追加精确静态集合，保留其网格、材质及变换，不调用体块替代。实际部件与模型身份写入 `actor-geometry.json`。源单位和朝向规范化后实测尺寸、接地、头与前向标识及颜色，不拉伸到登记期望值。四足体块和导入静态模型都不代表真实步态。

模型导入启动禁用自动执行；库数据追加不会运行模型内脚本。集合内只接受符合契约的静态 `MESH/EMPTY`，拒绝集合外依赖、链接/实例、骨架、动画/驱动、约束、修改器、形态键、UV/贴图等不支持输入。材质仅接受实体材质或严格常量 Principled 到 Surface 两节点结构，细则见模型复用文档。错误保留原模型选择和确认尺寸，只报告需要适配的项；不会自动补模型、清理后假称原资产，或偷偷降级成占位体。

角色只允许根位置和整体朝向变化；实际局部结构逐帧核对。普通项目登记不以旧的手部字段开启四肢。特殊手部诊断必须另行明确选择，`closeup_static` 不进入主 clean 视频。没有骨骼动画、IK、接触、碰撞求解或真实表演。

`--out` 必须新建或为空；失败产物保留后换新版本重跑。`--clip <id>` 可选片段，`--render none` 只保存工程，不能算渲染完成。`--fixture-unregistered` 仅用于明确的旧版合成回归，不能作为正式项目绕过登记的方式。没有 `--scene-blend` 的重建方式也不用于正式项目。

输出的 `scene.blend` 保持无人，片段子目录的 `previs.blend` 保存实际演员/相机。整数帧时间表保存完整 `storyboard_text` 与逐镜原文，而不是只有简述。保存工程后须在独立 Blender 进程重新打开、跳帧和复渲染；仅脚本内存中的画面或保存成功不证明复现。

`validate_blend.py` 的历史 demo 分支按逐帧记录检查相机与根位移；通用场景分支的相机覆盖范围取决于实际记录的样本。报告必须说明检查帧范围，不能把稀疏相机样本称为所有帧相机验收。

## 3. 同帧源双版本与全文绑定

对真实渲染得到的片段子目录运行：

```text
python <skill>/scripts/run_pipeline.py --project-root <project> --rendered-dir <project>/production/previews/meters/v005/registry_demo --timeline <project>/production/previews/meters/v005/registry_demo/timeline-v2.json --blender <installed-blender.exe> --font <Chinese-font.ttc> --mode compact --panel-height auto --max-chars-per-second 6 --min-page-seconds 2
```

`registry_demo` 是合成片段 ID，真实项目换成实际子目录。管线读取实际演员记录、完整源文和登记绑定，合成说明帧，分别编码两版，再完整解码检查与生成白名单。不要先手工创建同名媒体后再在非空输出上重跑。

说明版原图复制在顶部，文字栏扩展到下方；宽度不变、不遮挡、不缩小、不裁切。默认 `compact` 与 `--panel-height auto` 按真实字体、分页和阅读预算选择全片最小固定栏高，无 128/216px 硬下限；为编码偶数高度最多补 1px。也可明确指定正整数像素高。简述最多两小字号行且每页完整重复，完整对白按实际字形宽度换行。

默认每个非静默页至少 2 秒，每秒最多 6 个非空白 Unicode 字符，包含说话人和标点；简述不计入该数值，仍需人工判断总阅读负担。页面必须在原镜头帧区间内完整覆盖，对白源切片顺序拼接逐字还原原文，包含换行和空白。静默单页显示“（无对白）”，不继承前镜内容，不套用对白最短页时长。

空间或时间预算不足时明确失败；可以在新输出显式选 `--mode expanded`，不会自动切换，不截断、不加省略号、不延时。expanded 能展示全文，不保证短镜头内读得完。默认不绘制重复“说明/对白”标签和用途警示；可选 `--show-usage-hint` 只显示“预览视频仅供观看/上传生成请用干净版”。关闭提示不改变 `review-only`。

单独检查排版可用：

```text
python <skill>/scripts/dual_video.py --spec <clip>/timeline-v2.json --clean-frames <clip>/frames --output <new-review-frames> --font <Chinese-font.ttc> --mode compact --panel-height auto
```

`dual_video.py` 接受 `--max-panel-height`，默认 4096px；统一管线使用这一默认值。该命令只处理排版，不替代生产登记、实际编码或参考清单校验。

仅改说明版时，`run_pipeline.py --reuse-clean-from <current-output>` 可在新输出目录复用已验证干净 MP4 与对应源帧/完整解码帧；缺序列的归档不能假装含无损源帧。可选 `--expanded-review-from <verified-expanded-output>` 保存全文观看变体。所有复用须通过完整来源、模式、时间轴及当前登记检查，不能混入旧四肢人物、过期尺度或不同干净视频。复用参数属于 `run_pipeline.py`。

## 4. 检查编码、画面和源文字

两版保留真实原生 fps、帧数及切点，H.264 编码无音轨。完整解码核对实际 fps、尺寸和帧数；`mp4_timing.py` 只读解析本管线非碎片 CFR H.264 的 sample table，比较每帧展示时刻，不能用编码参数代替实测 PTS。

PNG 阶段逐帧比较说明版顶部与干净源完全一致。MP4 是分别有损编码，应报告实际误差：管线的 8 位像素平均绝对误差阈值为小于 3，源 PNG 对干净解码帧另要求 PSNR 大于 35dB。这是当前工程阈值，不是所有场景的普适质量标准。最终仍查看首中尾、切镜前后、分页切换、长中文与静默画面，实际检查字形和边界。

生成提示词使用完整片段 `storyboard_text`。每镜非空 `source_text` 必须在全文中恰好出现一次，`dialogue` 必须逐字包含于该镜原文，无对白用空串；缺省对白不能代替对原文的人工核对。可选 `storyboard_fields` 若提供则须包含 composition、camera、blocking、action、gesture、expression、sound 七项原文。UTF-8 覆盖报告证明文本保留，不证明模型理解或表演质量。

## 5. 当前项目绑定与干净白名单

新项目输出 `schema_version:3` 参考清单，在既有六项全文/代理绑定之外加入 `registry_binding`。片段目录保存：

```text
previs.blend
actor-geometry.json
timeline-v2.json
registry-binding.json
registry-snapshot.json
project-index-snapshot.json
source-spec.json
storyboard-source.txt
proxy-contract.json
generation-prompt.txt
generation-prompt-coverage.json
frames/
review_frames/dual-video-report.json
clean.mp4
review.mp4
decoded-clean/
decoded-review/
preview-manifest.json
upload-list.json
```

绑定和快照随结果一起保存；项目索引快照是追溯材料，不能替代当前项目。生成清单前运行：

```text
python <skill>/scripts/reference_guard.py <clip>/preview-manifest.json --project-root <project> --output <new-clean-upload-list.json>
```

若输出在项目树内，工具也可从最近祖先 `.timeaigc/project.json` 找对应项目；导出结果到别处需显式 `--project-root`。检查当前项目 ID、来源、实际模型字节、场景尺度与本片使用形态语义，重新核算冻结快照。只允许 `clean.mp4`，说明版及其同字节改名、诊断代理和过期绑定拒绝。

既有 v2.1 schema2 仍按旧契约兼容检查；schema3 始终要求当前项目，`--legacy-inspect` 不能绕过。不能删登记块、改 schema 或补哈希使旧画面冒充当前项目。保护是防误用，不是恶意视频语义鉴定器。

`dreamina_job.py prepare/submit` 延续清单和逐字 prompt 强制检查，实际提交前重验；它的外部服务依赖不因本地编码通过而自动可用。本轮不调用服务。项目交接 v1 清单与本段参考 schema3 清单分开保存，不改 schema 来迁就错误校验器。

## 留存证据

保留源场景、完整资料、确认来源、角色资产/字体/脚本版本与哈希、命令日志、退出码、实际渲染范围、世界尺度测量和最终媒体检查。尺寸/模型修订在新版本生成，旧结果只标过期，不改写历史字节。

交付报告分清实际执行、局部检查、历史复用及尚未支持的能力。本地 `.blend` 静态集合导入只按实际执行用例报告；不能扩大为任意格式/模型、IK、接触、真实表演或外部生成已验证。
