# 即梦任务执行

## 先核实能力

依次读取本地官方 `dreamina --help`、`dreamina multimodal2video --help`、`dreamina query_result --help`，以及需要调用的账户命令帮助。复用已有登录，不读取或输出凭据，不主动退出账号。

实际读取于2026-09-16：CLI 1.4.14/build b5ccc5d；`multimodal2video` 支持 `seedance2.0`、`seedance2.0fast`、VIP变体及mini，4–15秒；最多9图、3视频、3音频；非VIP一般720p。实时帮助与后端可用性优先，这不是Seedance 2.5能力证明。

随附适配脚本故意只实现已知4–15秒合同。当前帮助不含模型、关键flag改变、分辨率不支持时应失败；不要静默切换模型。帮助新增能力时可在项目副本适配并测试。

## 请求文件

保存UTF-8 JSON，路径相对请求文件：
```json
{
  "duration":5,
  "model_version":"seedance2.0",
  "video_resolution":"720p",
  "ratio":"16:9",
  "review":{"accepted":true,"reviewer":"user","basis":"用户已查看并确认本版预演；填实际记录"},
  "images":[{"path":"appearance/V01.png","role":"scene_appearance"}],
  "videos":[{"path":"render-current/short5/clean.mp4","role":"camera_and_blocking"}],
  "prompt":"逐字填入当前参考清单所绑定的generation-prompt.txt全文；本行只是占位示例"
}
```
此为字段示例，执行前必须填写真实审查记录，并逐字使用当前清单绑定的生成提示词，不能拿本例占位文字运行。无人值守授权下用`reviewer:agent`和真实依据；`accepted`只是审查记录，不授予新的付费权限。`images/videos/audios`都可为空但至少有图或视频。视频时长由ffprobe确认；`camera_and_blocking`角色要求与目标片长一致，其他参考片段按其用途检查。

当前prepare/submit要求v2参考清单具备完整 `proxy_reference`，把clean视频、源spec/分镜全文、实际演员模式/颜色记录、模式契约、提示词及覆盖报告绑定。请求 `prompt` 必须逐字等于已绑定提示词，并在submit前重验。历史仅含视频哈希的清单只可用 `reference_guard.py --legacy-inspect` 只读查看，不能上传。默认圆柱无手脚，浅色前条只表示方向；特殊分节模式需明确选择、`allow_special_proxy:true`和理由，`closeup_static`始终不进入clean。具体契约见[scene-contract.md](scene-contract.md)。

本轮仅验证本地管线和保护，没有付费服务验证；即使提示词明确禁止模仿圆柱、无肢体、僵直、滑行和符号，也不能保证生成模型不受这些内容影响。

## 使用脚本

```text
python scripts/dreamina_job.py prepare request.json --receipt prepared.json --reference-manifest preview-manifest.json
python scripts/dreamina_job.py submit request.json --receipt submitted.json --reference-manifest preview-manifest.json
python scripts/dreamina_job.py query request.json --receipt submitted.json --download-dir result
```

- `prepare`：只查帮助、校验和保存请求，**不提交**。准备记录与提交记录使用不同文件名。
- `submit`：真实上传并付费；仅在当前会话已有明确授权时使用。脚本调用subprocess参数数组，不拼shell命令，保留中文。
- `query`：查询已知ID，可下载生成结果。必须复用同一提交记录。
- `--cli`可指定真实可执行文件。

提交前检查余额及任务成本信息；现有账号余额不意味着无限预算。默认先一条短片、一条较长片，具体失败可有限修正；用户给预算时不得超过。返回的`credit_count`按任务原样记录，不把它冒充财务结算发票。

脚本先写`submitting`记录再启动CLI，防止中断后误重提。已有提交记录禁止覆盖；没有ID且状态未知时用`list_task --help`及任务历史查找，不创建新文件规避去重。超时只能说明本地未等到，不能推出远端未扣费或未接受。

`gen_status=querying`只表示排队/生成中；`success`才是服务完成；`fail`读取`fail_reason`。进程退出0不等于质量通过。下载后用ffprobe检查时长/尺寸/帧率，再抽取关键帧检查身份、布局、相机、人数与动作。

任务记录可能包含带时效参数的下载URL和用户信息，保留在用户本地项目，不发布到公开Skill仓库。发布仅包含通用脚本、规范和匿名样例。
