# 本地执行与环境

## 环境探测

寻找 `blender --version`、`ffmpeg -version`、`ffprobe -version`、可运行的Python；Pillow用于技术拼图，PyYAML仅Skill官方校验需要。Windows的`python`可能是WindowsApps占位符，此时使用实际运行时。显式UTF-8，不通过PowerShell标准输入传中文脚本。

依赖缺失时优先复用用户已安装位置。用户允许安装时安装Blender官方稳定版本与FFmpeg，Python依赖放任务虚拟环境；不要未经请求改动用户默认程序。无GUI也可后台批量渲染，无需Blender MCP服务器。

实际验证环境：Windows、Blender 5.2.1 LTS、工作台渲染引擎 `BLENDER_WORKBENCH`、Python 3与Pillow、FFmpeg。其他Blender版本先跑样例，不能根据版本号直接宣称兼容。工作台渲染依赖可用图形驱动；远程无显示设备环境可能需要EGL/虚拟显示或替代引擎适配。

## 命令顺序

当前默认读取资产Skill交付的无人场景，先按project-handoff.md校验清单与哈希。将scene-spec复制为本段预演数据，只添加clips，不擅改objects/views；模型与数据不符时先适配或退回资产修订，不用旧数据重建覆盖模型。

```text
blender --background --factory-startup --python-exit-code 1 --python <skill>/scripts/build_scene.py -- --scene-blend <asset>/model/scene.blend --spec <preview-spec.json> --out <new-preview-directory> --render clips
python <skill>/scripts/package_previews.py <new-preview-directory>
```

`--scene-blend`打开已有模型，不重建objects；在新目录生成预演并记录源文件哈希，不修改原文件。先检查源模型集合、单位、相机、隐藏状态及动画。碰撞采样以匹配的spec为依据，不证明任意导入网格无碰撞。复杂运镜用项目适配器。以下无`--scene-blend`命令仅保留旧项目/样例兼容，不是当前场景制作入口。

1. 复制 `assets/example-scene.json` 到项目，或创建符合契约的新数据。
2. `python scripts/scene_spec.py <spec> --floorplan <project>/floorplan.svg`：验证输入并出平面图。
3. `blender --background --factory-startup --python-exit-code 1 --python <skill>/scripts/build_scene.py -- --spec <spec> --out <project>/render-v1 --render all`。
4. `python <skill>/scripts/package_previews.py <project>/render-v1`：出四视图拼图、干净MP4、采样图。

`--render views` 只渲染四机位；`--render clips` 只渲染镜头；`--render none` 只建文件；`--clip short5` 选一段。即便不渲染片段也会生成选定片段的可编辑预演文件。`--out`用未使用的新目录，拒绝覆盖任何非空目录（包括中断后不完整输出）。

只有无`--scene-blend`的旧模式会重建环境，不能用它处理既有模型；当前模式显式传入资产模型，且只在新后台进程工作。`--factory-startup`避免用户插件干扰，`--python-exit-code 1`让脚本异常明确返回失败。Blender后台输出可能提示插件不能起服务，这不代表渲染失败；检查进程退出码与产物。

## 产物

```text
render-v1/
  floorplan.svg
  scene.blend
  manifest.json
  views/V01.png ... V04.png
  four-views.png
  four-views-labeled.png
  short5/
    previs.blend
    frames/00001.png ...
    samples.json
    previs.mp4
    contact-sheet.jpg
    first-frame.png
    last-frame.png
```

`manifest`记录输入哈希、Blender版本与警告。四视图为960×540；预演640×360，按片段fps渲染，编码为24fps H.264。需要更高分辨率、画幅或采样率时在项目适配脚本修改后重做全部相关参考，避免仅放大输出冒充细节。

`package_previews.py`只拼技术检查图与编码本地帧，不进行AI修图。它检查连续帧数量，避免上一版残留帧混入影片。MP4无声音；文字分镜负责对白和音效，不能把静音预演说成音画验证。

`build_scene.py`采样检查相机与演员中段位置是否进入方盒障碍。它不检查所有曲面、脚部、演员互撞、摄影机视线遮挡，也不生成安全路径。解决真实问题后重跑；仅在已人工审查合理交叉时用`--allow-warnings`并在报告解释。

## 恢复和修改

保留输入、生成提示词、已接受资产和任务ID。失败重跑选择新输出版本，完成前不删除旧版。定稿后可清理无用帧缓存，但保留可编辑文件、干净预演、关键帧、manifest与证据；不动源资料。
