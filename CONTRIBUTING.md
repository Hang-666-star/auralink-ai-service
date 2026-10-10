# 协作与发布

日常开发从最新 `main` 创建功能分支，提交 PR 到 `main`。写清问题、修改后的行为、实际验证结果和发布影响，由项目负责人审查后合并。`main` 用于集成变更，`release` 用于记录已批准发布的版本。

发布时从**本仓库的 `main` 向 `release`** 创建 PR。禁止把功能分支或 fork 的同名 `main` 直接合入 `release`。`AI Service CI` 会验证分支名和来源仓库。`AI source checks` 继续作为既有必需检查，包含源码编译与 Guide/Critic 契约测试；`release` 还需 `Verify main-to-release promotion`。两类服务测试失败都会使已有必需检查失败。

## 检查范围

CI 使用与当前生产服务一致的 Python 3.10。`AI source checks` 编译 Guide、Critic、三个旧服务和测试目录的第一方 Python，要求当前服务的模块、依赖文件及旧服务关键入口存在。该步骤不导入服务，也不编译 `vmm-service/third_party` 中的 AudioCraft 快照。

同一个 `AI source checks` job 随后安装两个轻量服务的声明依赖，通过进程内客户端验证配置、鉴权、健康/就绪、请求结构及确定性本地评价流程。测试隔离私有环境配置并阻止 provider 网络传输，不使用真实密钥或 GPU。

语法和离线契约检查不能证明真实 provider 的连通性与生成质量，也不能证明旧 GPU 服务可加载或推理正确。涉及这些内容的 PR 应记录相应环境、执行命令、结果及未完成事项。不要为了通过 CI 在生产环境原地重装依赖或整套 GPU 环境。

## 资源与密钥

模型权重、模型缓存、运行数据库、生成音频、真实环境文件和密钥不进入 Git。使用可提交的示例配置记录所需变量，实际值由服务器配置提供。更新模型或 GPU 依赖前，核对当前运行环境、磁盘空间和恢复方式。

## 当前发布状态

当前源码迁入和 CI **尚不执行 release 合并后的生产部署**。接通发布需通过 Deploy 的 R25 统一控制，核验组件提交 SHA、源码/环境包装器摘要绑定、进程身份与健康/就绪，并保留完整回滚状态，详见 [R25 发布边界](docs/r25-ai-services.md)。

Guide/Critic 已确认属于当前国画项目并纳入本仓库；运行和部署职责由 Deploy 管理。MusicGen、Vision、VMM 保留源码，当前 R25 发布不启动它们。
