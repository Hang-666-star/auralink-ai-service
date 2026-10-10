# Guide Service

Guide 是 Core 调用的回环 Flask 服务，使用结构化画作资料与知识条目生成中文导赏，不加载 Torch、Transformers 或 AudioCraft。

- `GET /health` 返回 `status: UP`、`providerConfigured` 和 `modelConfigured`。HTTP 200 只说明健康接口可响应；两个布尔值必须结合发布配置核对，且此接口不验证 provider 网络是否可用。
- `POST /v1/guide/generate` 要求 `X-Auralink-Internal-Token`，接收 `schemaVersion: "1"` 的请求。内部鉴权失败在资料校验和 provider 调用前返回 401；内部令牌未配置时返回 503。
- 导赏结果包含摘要、八个章节、亮点与来自输入的知识引用。无效结构被拒绝，不把 provider 返回的任意文本当作成功。

配置来自进程环境及 `AURALINK_ENV_FILE` 指定的私有环境文件。指定路径必须为绝对路径；未指定时沿用源码相邻 `backend/.env` 的兼容默认。环境文件按静态 `KEY=VALUE` 数据解析，进程已有值优先，不执行 shell 或变量展开。本仓库不提供生产环境文件。

当前代码使用 `QWEN_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL` 与 `AURALINK_GUIDE_INTERNAL_TOKEN`，支持模型标识由源码约束为 `qwen3-vl-plus`。绑定地址必须是 `127.0.0.1` 或 `::1`；R25 通过运行配置选择端口 19084，源码缺省端口为 5003。

启动模块为 `python -B -m guide_service`，由 R25 运维控制注入配置并管理进程。不要用旧文档中的开发脚本替换生产启动器。生产操作说明见 [R25 发布边界](../docs/r25-ai-services.md)。

网络请求只在已证明尚未发送请求的连接失败时允许一次重试。超时、结果不明的断连、429 或 5xx 不自动重放，避免重复提交可能已被 provider 执行的请求。

本地测试从仓库根目录执行 `python -m pytest -q tests/test_guide_contract.py`。确定性测试模式只用于验证数据契约与调用链，不提供真实模型质量证据。
