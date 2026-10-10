# Guide / Critic 的 release 发布

日常功能分支先合入 `main`，通过现有 `AI source checks`，再由同一仓库的 `main` PR 合入 `release`。`Release R25 Guide and Critic` 工作流只接受 `release`，重新执行离线服务契约测试和候选完整性测试。

候选固定包含当前 R25 的 19 个 Python/requirements 文件，并记录完整 commit、Actions run/attempt、逐文件 SHA-256、依赖摘要和归档摘要。创建器确认本地字节等于该 commit 的 Git blob。旧音乐服务、环境文件、凭据、模型、图片和缓存不进入候选。

部署由 Deploy 仓库审核后的固定服务器控制器执行；候选不能携带或选择服务器脚本。生产 `SERVER_HOST`、`SERVER_PORT`、`SERVER_USER`、`SERVER_SSH_KEY` secrets 和 `SERVER_HOST_KEY` 公钥变量在 `production` environment 配置。仓库变量 `R25_DEPLOY_ENABLED` 只有为字符串 `true` 才建立服务器连接；默认关闭时 summary 显示 `NOT_DEPLOYED`，仅保留候选 artifact。

目前生产控制器尚未安装，部署开关保持关闭。后续开启前依照 Deploy 的 `r25/AI_RELEASE_CONTRACT.md` 安装与演练，不把候选上传成功当作生产发布完成。

实际源码升级与 Core/Web 共用部署锁。控制器暂停 owned Java/watchdog 后再次确认 AI 任务空闲，只重启发生变化的 Guide/Critic，验证真实健康、内部鉴权与配置，再恢复原 Core 版本和 watchdog。Web/Tunnel 保持运行。失败时根据私有事务记录恢复原源码；恢复不完整时拒绝后续启动/发布。

当前契约允许审核后的源码变化，拒绝 requirements 变化和未纳入清单的新模块，不在生产 Python 环境现场安装依赖。新增依赖或运行模块应连同 Deploy 契约单独审查。源码与现有生产完全相同时只返回 `AI_IDENTICAL_SOURCE_REGISTERED`；实际改动部署返回 `AI_SOURCE_PUBLISHED`。生产运行验证仍需另行记录。

本地创建候选需要真实 Git checkout，且 `--commit` 为当前 HEAD：

```bash
python scripts/ai_release_candidate.py create --source . --output dist/r25-ai \
  --repository Hang-666-star/auralink-ai-service --ref refs/heads/release \
  --commit <full-commit> --run-id <actual-run-id> --run-attempt <actual-attempt>
```

不要为手工源码副本编造 Actions 身份；实际工作流会自动填入以上值。测试可以使用隔离 fixture 验证结构和拒绝场景，不能把 fixture 当作可部署的候选。
