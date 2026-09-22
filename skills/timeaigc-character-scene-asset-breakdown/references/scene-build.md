# 静态场景数据与执行

## 数据

scene-spec.json使用 `schema_version:1`、scene_id、`units:"meters"`、`up_axis:"Z"`、`bounds:[xmin,ymin,xmax,ymax]`、objects、views；本阶段clips为空。另记source、assumptions、state_version。

物体示例：`{"id":"table","label":"木桌","kind":"box","role":"prop","location":[0,0,0.5],"size":[1.2,0.8,0.1],"color":[0.55,0.35,0.2],"rotation_z":0}`。

kind为box/cylinder/sphere，role为architecture/prop。location是中心，size为完整尺寸（不是半径）；旋转角度制。ID稳定唯一，RGB为0–1。plan_hidden只隐藏平面图的非关键细节，不能隐藏核心占地；collidable:false不用于掩盖真实通道冲突。用分段墙留真实门窗开口，墙厚、门扇开启范围与动线可解释。

views恰好四项V01–V04，各有position、target和lens（脚本允许10–200毫米，缺省28，传感器宽36毫米；允许范围不是推荐焦段）。V01匹配主图，其他视角依 [空间场景制作](spatial-scene.md) 的焦段、覆盖与相邻视图规则选择，显式写入试拍后确定的lens。四机位为透视参考，不是工程正投影。画幅与后续生成统一，不能仅修改生成平台ratio。

## 人工机位接管

用户要求亲自调机位时，用实际安装的Blender打开场景工作副本的可见窗口。先检查已有会话及保存状态，避免覆盖用户未保存修改；这是用户需要操作的窗口，不使用后台渲染模式代替。

通过可用Blender接口或在项目内准备的启动脚本，按清单camera_id查找实际CAMERA对象，设置为scene.camera并设为活动选中对象。将当前工作区可见VIEW_3D的region_3d.view_perspective设为CAMERA；若视口启用了独立摄像机，也将该视口摄像机指向同一对象。按需要开启lock_camera，明确告知用户视口导航会调整摄像机。没有可见三维视口时先切到含三维视口的工作区。不能只修改场景活动摄影机而不切换视口。

用可用界面状态或Blender接口回读确认文件路径、活动摄影机及视口摄像机模式，再告知已打开到指定视图；仅进程启动成功不等于界面已就绪。保存并恢复用户实际调整后的相机时，以blend为准，不调用会清空场景的build_scene.py。变更记录、简模确认和成品更新遵循 [空间场景制作](spatial-scene.md) 的单个视图修改流程。

## 执行

先探测可运行的Python、Blender及Pillow。Windows优先使用已安装的实际Python，避免WindowsApps占位程序；显式UTF-8。安装遵循当前授权，不更改默认程序。后台Blender不需要启动MCP服务器。

以下命令中的Skill与项目路径均需替换为实际绝对路径，产物不写进Skill：

```text
python <skill>/scripts/scene_spec.py <scene-spec.json> --floorplan <project>/floorplan.svg
blender --background --factory-startup --python-exit-code 1 --python <skill>/scripts/build_scene.py -- --spec <scene-spec.json> --out <new-model-directory>
python <skill>/scripts/package_views.py --images <V01.png> <V02.png> <V03.png> <V04.png> --out <four-views.png>
```

build_scene.py会新建环境，只用于新建场景，拒绝非空输出目录及含动态片段的数据。不要传已有用户blend让其重建。既有模型用项目副本适配，并保持scene-manifest映射。输出scene.blend、floorplan.svg、views/V01–V04.png、manifest.json。几何基础为简单体块，复杂斜坡、曲墙和建模需求用项目脚本扩展，不虚称脚本自动理解主图。

package_views.py仅等比排版技术拼图，按传入V01–V04顺序排列，不生成美术、不裁掉内容。分别对四张简模图和四张已接受成品图执行；缺一张就不生成“完整四视图”。成品V01保留原图文件，拼图缩放不修改源图。

查看实际图像，检查墙体遮挡、比例、共同地标、无人和机位覆盖。记录Blender版本、输入哈希及人工审查；文件生成成功不代表主图精确还原。标准脚本采用工作台渲染，依赖可用图形驱动；无显示环境失败时如实处理，不用空图替代。
