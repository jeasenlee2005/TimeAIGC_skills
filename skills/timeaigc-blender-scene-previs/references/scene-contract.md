# 空间、固定人物与双版本数据契约

v2.2 正式项目先按 [角色登记与尺度](character-registry.md) 完成全项目预检，再由 `build_scene.py --project-root ... --scene-blend ...` 解析角色引用和单位。下文的直接身高演员输入是旧版独立合成数据格式，不能替代 v2.2 项目登记。正式片段只给 `id/character_id/form_id/keys`，尺寸、颜色和模型从登记解析，输出采用 schema3 参考绑定。

当前流程从已登记资产scene-spec复用根字段与静态objects/views，只在预演版本添加clips。静态建模与补四视图由资产Skill负责；本文件保留字段说明及历史样例以兼容既有数据。已有blend通过--scene-blend读取，不按本文件重新搭一套场景。

场景输入仍为 `schema_version:1`，本副本增加固定人物朝向与说明文本字段；导出的整数帧时间表及参考白名单采用各自v2结构。不要把v2时间表直接交给v1场景校验器，也不要把v2参考清单当成 `validate_handoff.py` 接受的v1项目交接清单。

## 目录
- 根字段与实体
- 四机位
- 片段与代理
- 可复用样例与范围

## 根字段与实体

`schema_version: 1`、`scene_id`、`units: meters`、`up_axis: Z`、`bounds: [xmin,ymin,xmax,ymax]`、`objects`、`views` 必需，`clips` 可空。推荐另外保存 `source`、`assumptions`、`state_version`。坐标为物体中心，角色位置例外：代理底面根节点。

每个物件：
```json
{"id":"table","label":"木茶几","kind":"box","role":"prop","location":[0,0,0.5],"size":[1.2,0.8,0.1],"color":[0.55,0.35,0.2],"rotation_z":0}
```

- `id` 唯一且稳定；`kind` 为 `box/cylinder/sphere`，`size` 为三个完整尺寸，不是半径；旋转单位为角度。
- `role` 为 `architecture/prop`；角色必须在片段 `actors` 中，避免污染无人资产。
- `color` 为0–1 RGB；`bevel` 默认0.025米，仅影响立方体倒角。
- `collidable:false` 可用于地毯等非阻挡薄片，不用于消除真实墙/家具碰撞警告。
- `plan_hidden:true` 只影响平面图，适合桌腿、靠背和门窗上梁；不得隐藏造成误解的核心占地。
- 墙、门、柜子、桌面均为简单体块；复杂多边形可拆为若干旋转体块。楼梯、斜坡、曲墙、骨架等超出v1时用项目脚本扩展，先明确扩展契约。

## 四机位

`views` 恰好四项：`id`、`position:[x,y,z]`、`target:[x,y,z]`、`lens`（10–200mm，默认28，36mm传感器宽）。视角ID作为PNG文件名，不得含路径分隔符。所有机位共用同一环境集合。

默认透视画幅16:9；需要竖屏时同时修改渲染尺寸和生成目标，不仅修改即梦ratio。镜头图并非正投影，不用透视图直接测量工程尺寸。

## 片段与代理

每段：`id`（安全文件夹名）、`duration`（秒）、`fps`（默认12，整数1–60）、`shots`、`storyboard_text`、可选 `actors`、`width`/`height`（本副本渲染默认960×540）。通用scene输入必须在每个 `clips[]` 项中填写该片段的完整 `storyboard_text`；仅在外层scene顶层填写不满足要求，也不自动继承。导出的该片段 `timeline-v2.json` 将它保存为自身顶层字段，供模式契约和全文提示词绑定。每镜：
```json
{"id":"S01","start":0,"end":5,"source_text":"原文：从门口看人物进入，缓慢推近。甲：我回来了。","summary":"门口中景，人物进入后停下。","dialogue":"甲：我回来了。","camera_start":{"position":[0,-3,1.6],"target":[0,1,1.2],"lens":32},"camera_end":{"position":[0,-2,1.6],"target":[0,1,1.2],"lens":32}}
```
`camera_end` 缺省表示固定机位。位置、目标和焦距线性插值；不是自动贝塞尔绕行。需要曲线轨迹/升降/环绕可拆连续子镜头并保留原镜头关联，或在项目脚本实现曲线，避免引入实际剪切。镜头不能时间重叠或留白。

`source_text` 保留每镜完整原文；`summary` 是人看的简述，不能替代原文。`dialogue` 是原对白字符串，含说话人和标点；无对白使用空串。缺省空串并不证明原分镜无对白，转换时必须人工核对，不能把未提取对白当静默镜头。用于生成绑定的片段还需保留完整 `storyboard_text`，供逐字覆盖检查。对白不会被合成成声音或口型。

演员：
```json
{"id":"actor_a","character_name":"甲","color_name":"蓝色","height":1.76,"color":[0.2,0.3,0.5],"proxy_mode":"cylinder","hand_lod":"none","keys":[{"time":0,"position":[0,2,0],"yaw_degrees":0},{"time":2,"position":[0,1,0],"yaw_degrees":90},{"time":5,"position":[0,1,0],"yaw_degrees":90}]}
```
键帧必须首0末duration、递增；位置为代理底面根节点，Z向上，人物未旋转时正面为局部-Y。`yaw_degrees` 为绕Z轴的角度，缺省0，按相邻键线性插值，不自动选择最短转向；跨360度时应明确连续角度，避免意外长转。局部-X是角色自身右侧，不能按画面左右猜手侧。根位移和整体旋转逐帧烘焙。

所有模型参考人物由 `canonical_actor.py` 同一版本生成，当前版本 `timeaigc-neutral-2.0.0`。底层兼容示例默认身高1.76m，v2.2 允许0.05–100m统一缩放以容纳已确认巨型角色；正式项目必须使用登记总高而非默认值；颜色为0–1 RGB，不随机改变身材。默认 `proxy_mode:cylinder`：圆柱躯干、头部、低调鼻部前向提示和贴合躯干的浅色前条；无手脚、肩臀及分节四肢。实际网格部件名是 `body_cylinder`、`head`、`nose`、`front_marker`，完整尺寸以实际 `actor-geometry.json` 为准。前条仅表示朝向，不是衣襟、服装或身份。此结构是可复现空间占位，不是医学人体模型。历史1.1和更早资产及MP4保留原字节，不能仅改版本标签冒充新圆柱结果。

| 默认圆柱参数 | 比例 | H=1.76m时 |
| --- | --- | --- |
| 躯干高度、半径 | `0.82H`、`0.115H` | 1.4432m、0.2024m |
| 头部XYZ完整尺寸 | `(0.17H, 0.16H, 0.18H)` | `(0.2992, 0.2816, 0.3168)m` |
| 头中心Z | `0.91H` | 1.6016m |
| 鼻部中心XYZ | `(0, -0.083H, 0.912H)` | `(0, -0.14608, 1.60512)m` |
| 前条竖直范围 | `0.58H–0.70H` | 1.0208–1.2320m |
| 前条角度范围 | 正面中心左右各5° | 总10°，随64边圆柱实际表面贴合 |
| 前条表面间距、厚度 | `0.00005H`、`0.00040H` | 0.088mm、0.704mm |

前条仅用微小间距避免深度闪烁，不是悬浮胸牌；默认材质RGB `(0.88,0.89,0.86)`。身高缩放也缩放其间距与厚度；仍需实际观察正侧背面。

`proxy_mode` 可在演员或片段/spec中声明，演员字段优先；省略时为 `cylinder`。圆柱规范手部字段是 `hand_lod:none`，旧输入 `standard` 仅为兼容接受并规范化为 `none`，不会生成手。圆柱不接受 `closeup_static`。

`articulated_special` 必须明确选择，并在片段/spec同时设置布尔值 `allow_special_proxy:true`、非空字符串 `special_proxy_reason`。只有这种特殊模式可选 `hand_lod:standard` 的固定轻量手；`closeup_static` 为连续五指静态分析手，始终是 `diagnostic-only`，即使具备特殊许可也不能进入clean或生成白名单。普通镜头不能因为旧手部字段自动恢复四肢，不能把只填许可当作已选择特殊模式。所有模式都没有骨骼、IK、接触求解、坐卧、步态、手势、表情、持握或口型；每帧比较局部几何记录，禁止子件表演动画。

身份颜色映射使用 `id`、`character_name`、`color_name` 和实际 `color`。缺省 `character_name` 时使用输入id，缺省 `color_name` 时使用实际RGB换算的十六进制标记；脚本不据颜色猜角色身份，正式项目应明确填写名称。源输入、实际记录与提示词中的演员集合、模式、身高及RGB必须一致，禁止伪填模式或色彩。

保留根节点下四个非渲染 Empty，对象名为 `<actor_id>__<locator_id>`，用途 `non-rendering-construction`，`hide_render=True`。下表为未旋转根节点坐标中的米制位置，H为输入身高；它们随根节点整体运动，局部位置固定，不增加眼睛网格、约束、驱动或子件动画。

| locator_id | 默认cylinder局部位置 / H | 特殊articulated_special局部位置 / H | 用途 |
| --- | --- | --- | --- |
| `eyeline_center` | `(0, -0.076, 0.938)` | `(0, -0.054, 0.950)` | 中立眼线中心，供特写构图定位 |
| `eye_R` | `(-0.026, -0.072, 0.938)` | `(-0.026, -0.054, 0.950)` | 角色自身右眼位置构造点 |
| `eye_L` | `(0.026, -0.072, 0.938)` | `(0.026, -0.054, 0.950)` | 角色自身左眼位置构造点 |
| `gaze_target` | `(0, -0.620, 0.938)` | `(0, -0.620, 0.950)` | 中立正前方目标构造点 |

`locators` 与网格 `parts` 分开记录；当前圆柱为4个网格部件加4个Empty，不沿用历史20件网格默认断言。默认1.76m圆柱眼线高度为1.65088m；特殊代理的旧0.95H定位值不能套用到新圆柱。这些构造点并非从原图测得的真实眼位，也没有自动凝视或眼神表演能力。历史资产没有locator时保持历史语义记录，不能伪造旧版本已含该功能。

时间换算：秒t对应 `round(t*fps)+1`；片段共 `round(duration*fps)` 帧，末帧处在duration−1/fps。镜头相机端点落在该镜最后一帧；角色按各帧真实时间取样。它们不是物理模拟。

## 整数帧时间表与说明栏

每段输出 `timeline-v2.json`：

```json
{"schema_version":2,"width":960,"height":540,"fps":12,"frame_count":60,"storyboard_text":"测试片段：原文：从门口看人物进入，缓慢推近。甲：我回来了。","actors":[{"id":"actor_a","character_name":"甲","color_name":"蓝色","height":1.76,"color":[0.2,0.3,0.5],"proxy_mode":"cylinder","hand_lod":"none"}],"shots":[{"id":"S01","start_frame":0,"end_frame":60,"source_text":"原文：从门口看人物进入，缓慢推近。甲：我回来了。","summary":"门口中景，人物进入后停下。","dialogue":"甲：我回来了。"}]}
```

此处是最小数据结构示例，不替代项目的完整分镜或相机/根位移信息。新管线使用实际构建的演员记录与这些输入字段共同校验，不能仅凭这个JSON生成真实已验证清单。`start_frame`/`end_frame` 使用零基、半开区间 `[start,end)`，必须完整连续覆盖 `[0,frame_count)`，至少一帧，无空洞或重叠。PNG `00001.png` 对应时间表帧0。两版使用相同fps/帧数，切点k的说明从第k帧开始切换，前一帧仍属于旧镜。

延续2.1的 `dual_video.py` 逐镜按字体实际像素宽度换行，保留原换行、空格和标点；记录每行源字符范围并验证重构与原文相等。默认 `--mode compact --panel-height auto`，auto可省略。排版与分页共同寻找满足全片内容和原镜头阅读预算的最小固定栏高，不设216px或128px硬下限；编码偶数高度最多补1px。可用正整数显式覆盖栏高，数值上限16384并受 `--max-panel-height` 默认4096px约束，过小/超限明确失败，不以省略号、缩字或裁图补救。

页眉显示镜号、秒区间和页码；完整简述每页重复，最多两小字号行，超出时明确报错并建议expanded。不绘制重复“说明/对白”标签，默认不显示用途警示。若显式启用 `--show-usage-hint`，只允许“预览视频仅供观看/上传生成请用干净版”这一句，其文字高度计入自动排版。关闭提示不改变清单中的 `review-only` 或 `allowed_for_generation:false`。

对白页面按原镜头时长分配：`--max-chars-per-second 6` 与 `--min-page-seconds 2` 为默认阅读预算。计入预算的是对白去除所有空白后的Unicode字符数，含说话人标记和标点；简述不计入此数字，仍需人工判断总阅读负担。空串或纯空白对白为全镜单页静默，跳过非静默页的最短阅读时间要求。非静默页所需帧数至少为 `ceil(max(非空白字符数/最大字符速率, 最短页秒数) * fps)`。所有页面必须在所属镜头整数帧区间内无缝铺满，不跨切镜、不延长时间、不插入停格。

紧凑排版无法在栏高上限和原阅读预算内成立时明确报错，建议在新输出目录改用 `--mode expanded`，不会自动切换模式。展开模式一次显示完整说明和对白，按全片最高需求确定统一栏高；`--max-panel-height` 默认4096px，超限拒绝。展开版保留全文但不保证短镜头内能读完；阅读限制留在报告中，不绘制自动警示文案。两种观看模式都不能截断、缩小或裁切原画面。

`dual-video-report.json` 使用schema v2，记录 `layout_mode`、实际 `panel_height` 和 `review_height`。`pagination_settings` 声明计数/简述/静默规则及 `timing_modified:false`，并记录 `panel_height_selection`（auto/explicit）、`panel_height_requested`（auto为null）、`minimum_feasible_panel_height`（compact自动选择时的最小可行值）、`usage_hint_shown` 和 `usage_hint_text`。每个 `shots[].pages[]` 含页号、总页数、`start_frame`/`end_frame`、源对白 `source_start`/`source_end`/`source_text`、`reconstructed`、非空白字符数、实际字符速率和 `required_reading_frames`。报告另含 `frames[].page_index`、`page_change_frames`、`all_page_text_preserved`、`all_page_intervals_cover_shots`。逐页源切片拼接必须等于原串，包括换行和空白；页面帧区间必须恰好覆盖所属镜头。文本完整、阅读预算合规和最终字形可读分别检查。

说明帧保持宽W，顶部H像素原样复制干净RGB/RGBA帧，只在下方新增说明区域；写盘后逐帧比对上部像素。输出为 `review_frames` 和 `dual-video-report.json`，其中 `role: review-only`、`allowed_for_generation:false`。空白或空对白显示一行“（无对白）”，只是观看提示，不写回原台词。

## 双版本参考白名单

以下 schema2 说明保留 v2.1 兼容规则。v2.2 项目实际输出 schema3，另含 `registry_binding` 及登记/索引快照，必须对照当前项目验证，详见 [角色登记](character-registry.md) 与 [执行方法](execution.md)。不得删绑定或降级 schema 绕过当前性检查。

参考保护使用单独v2清单，至少含 `files.clean_video`、`files.review_video` 和 `reference_policy.allowlist`。同时保留全文对照时，`files.review_variants` 另列 `review-expanded.mp4` 及其 `layout:expanded`。每条媒体记录使用相对清单目录的规范 `/` 路径和小写SHA-256；允许项恰好为干净视频一条，`purpose:model-reference`；紧凑与展开说明版均为 `purpose:review-only`。两版路径和字节哈希必须不同。`reference_guard.py` 检查实际文件、路径、哈希与清单绑定，拒绝任一说明版、非白名单副本、路径别名和文件变化；不分析视频像素语义。复用旧干净MP4只复用已核验的字节与时间轴，不把旧说明版改成干净参考。

新生成入口还要求以下 `proxy_reference` 绑定。这里的路径均相对清单目录，SHA-256为对应实际文件哈希；`clean_video_sha256` 必须等于本清单白名单中的干净视频哈希。

```json
{"schema":"timeaigc-proxy-reference/v1","purpose":"model-reference","clean_video_sha256":"<clean.mp4的SHA-256>","mode_contract":{"path":"proxy-contract.json","sha256":"<SHA-256>"},"source_spec":{"path":"source-spec.json","sha256":"<SHA-256>"},"source_storyboard":{"path":"storyboard-source.txt","sha256":"<SHA-256>"},"actor_records":{"path":"actor-geometry.json","sha256":"<SHA-256>"},"generation_prompt":{"path":"generation-prompt.txt","sha256":"<SHA-256>"},"prompt_coverage":{"path":"generation-prompt-coverage.json","sha256":"<SHA-256>"}}
```

`proxy_contract.py` 的模式契约使用 `schema:timeaigc-proxy-contract/v1`，记录源spec、实际演员记录的规范哈希以及演员映射、实际部件、特殊许可和理由；从受控构建的真实记录重算校验，不能只相信请求字段。`generation_prompt.py` 根据模式契约和源spec生成提示词与覆盖报告，逐字保留 `storyboard_text`、每镜 `source_text` 和 `dialogue`；绑定的源全文文件必须逐字等于spec中的 `storyboard_text`。每镜原文须在全文中恰好出现一次，原对白必须逐字包含于该镜原文，保留CRLF。可选 `storyboard_fields` 若提供，须含composition、camera、blocking、action、gesture、expression、sound七个非空字符串，且各值都出现在source_text中。覆盖报告使用 `timeaigc-generation-prompt-coverage/v1`，记录原文/对白的UTF-8字节范围及哈希。它证明文本保留，不证明原文语义完整、模型理解或遵循。

`dreamina_job.py prepare/submit` 需要 `--reference-manifest`，在外部CLI调用前校验白名单及完整绑定，提交前再次校验；请求 `prompt` 必须逐字等于已绑定提示词。缺少绑定的历史清单默认拒绝生成，不能改名或重填哈希冒充新结果。`reference_guard.py --legacy-inspect` 仅作只读历史检查，输出 `legacy-unverified` 和 `generation_eligible:false`，不能同时使用 `--video` 或 `--output`。旧文件仍可保存为历史，不等于可直接生成。

## 可复用样例与范围

`assets/example-scene.json` 是历史匿名客厅合成数据，只有显式 `--fixture-unregistered` 才用于旧格式回归，不能作为正式项目入口，也不能当作用户剧本事实。v2.2 完整接入示例在交付根 `demo-project/`。其5秒和14秒镜头用来展示数据组织，不是强制镜头数或叙事模板。

保存正式项目原分镜，不只保留转换后的JSON；源资产有矛盾时在假设中说明选用依据。不同场景、回忆/现实和破坏状态分别引用正确空间版本，不能跨切换丢失状态。

