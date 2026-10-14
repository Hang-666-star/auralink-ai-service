# AuraLink AI 服务

当前国画项目由两个轻量 Python 服务提供导赏和评价能力，业务数据、任务与用户接口由 `auralink-core-api` 管理。

| 目录 | 用途 | R25 运行方式 |
| --- | --- | --- |
| `guide_service/` | 按画作资料生成结构化导赏 | Python 3.10，`python -B -m guide_service`，回环端口 19084 |
| `critic-service/` | 四维专家两轮复议与综合评价 | Python 3.10，`python -B -m uvicorn app.main:app`，回环端口 19085 |
| `musicgen-service/`、`vision-service/`、`vmm-service/` | 保留的音乐生成与本地模型代码 | 当前 R25 发布不启动 |

Guide/Critic 从已核查的 R25 运行源码恢复，服务代码与两份 requirements 在本次迁入中保持原始字节。虚拟环境、生产环境文件、内部令牌、provider 密钥、图片、模型与数据库不进入仓库。生产服务由 `auralink-deploy` 的 R25 运维控制管理；此仓库中的 CI 不操作生产进程。

## 本地验证

在仓库根目录使用独立 Python 3.10 虚拟环境：

```bash
python -m venv .venv
# 激活虚拟环境后执行：
python -m pip install -r guide_service/requirements.txt -r critic-service/requirements.txt
python -m pytest -q tests
```

依赖安装需要访问包索引；测试本身通过进程内 HTTP 客户端和确定性本地 provider 运行，不使用真实密钥、不请求模型、不需要 GPU。测试隔离私有环境文件，并阻止 Guide/Requests/HTTPX provider 传输路径。

两服务的依赖在 CI 中可装入同一测试环境；生产沿用各自经过验证的虚拟环境。Critic 的 requirements 含版本范围，本次测试成功不等于新的依赖组合已经适用于生产，不应在发布时原地重装生产依赖。

## 协作与发布

功能分支通过 PR 合入 `main`；阶段结果仅通过同仓库 `main` → `release` PR 晋级。CI 保留旧服务第一方源码编译检查，并增加 Guide/Critic 配置、鉴权、健康状态和真实离线接口流程检查。`Verify main-to-release promotion` 检查发布来源。

生产发布需经过 R25 统一发布控制、版本绑定、健康/就绪验证及回滚流程。详见 [R25 发布边界](docs/r25-ai-services.md)、[贡献说明](CONTRIBUTING.md) 和各服务说明。
