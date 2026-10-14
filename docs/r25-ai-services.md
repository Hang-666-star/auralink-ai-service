# R25 AI 服务发布边界

本次变更将当前国画项目的 Guide/Critic 运行源码纳入版本管理。源码、依赖声明、测试和说明属于 AI 仓库；进程控制、私有配置注入、跨组件切换和回滚属于 Deploy 仓库。Core/Web/AI/Deploy 四个仓库应在发布记录中保留各自提交 SHA。

## 本次迁入范围

Guide 的 8 个 Python 文件与 requirements.txt，以及 Critic 的 9 个 Python 文件与 requirements.txt，与迁入前独立采集的 19 个运行文件 SHA-256 完全一致。另保留 Critic 的 pyproject.toml；两份旧说明已按当前仓库结构重写。生产目录、原始审计压缩包、环境文件和内部运维元数据不属于仓库发布内容。

这是一份源码基准恢复，不修改原有服务逻辑。新增测试验证真实应用的内存 HTTP 接口、配置和确定性 stub；它们不证明真实 provider 的连通性、质量或生产进程已经切换。当前 CI 没有生产写入步骤。

## 发布必须核验的事实

| 服务 | 入口 | 绑定 | 配置就绪检查 |
| --- | --- | --- | --- |
| Guide | `python -B -m guide_service` | 回环 19084 | `/health` 的 `providerConfigured`、`modelConfigured`，内部令牌配置和进程身份 |
| Critic | `python -B -m uvicorn app.main:app` | 回环 19085 | 认证的 `/api/v1/readiness`：`evaluation_available: true`、`execution_policy: configured-provider` |

两服务的 HTTP 200 都不足以确认真实 provider 请求可完成。生产验证应分开记录进程与配置检查、Core 到服务的内部契约检查，以及经授权的实际 provider 验证结果。读取就绪接口本身不消耗模型调用。

R25 现有 lifecycle 对 Critic main/config/llm 及环境包装器存在摘要绑定。生产发布不能直接覆盖代码再重启，而需在同一个受控操作中核验来源、保存完整旧版本与绑定、安装候选源码和对应绑定、确认进程加载新版本，并在失败时恢复旧状态。共享锁与版本记录必须覆盖 Core、AI 和 Deploy 之间的关系。

第一步交付保持源码基准不变。后续 release → Actions → R25 的部署步骤须调用既有统一发布控制，不能另设 SSH 命令直接管理 PID、绕过版本绑定，或启动旧 Compose 中的 MusicGen/Vision/VMM。发布动作尚未接入时，应把运行结果标为构建/检查完成，不记为部署成功。

生产虚拟环境沿用现场验证版本；CI 的依赖安装不授权在生产原地 `pip install`。后续依赖升级应在独立候选环境验证，并与代码版本一起具备回退能力。
