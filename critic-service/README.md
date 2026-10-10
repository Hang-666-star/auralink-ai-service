# Critic Service

Critic 是 Core 调用的 FastAPI 国画评价服务。四位专家分别观察，再共享意见复议，最后由主持人汇总；一次完整流程包含九个模型步骤，返回结构化意见，不打分。

支持 `authoritative_four` 和 `requested_four` 两种方案。`GET /api/v1/evaluation-profiles` 返回具体维度；`POST /api/v1/evaluations` 接收 `image`、可选 `profile`、`title` 和 `context`。评价接口要求 `X-Auralink-Critic-Token`，鉴权先于图片处理和模型客户端创建。

## 健康与就绪

`GET /health` 返回 `status`、版本、provider/model 标识、`model_configured`、`vision_capable` 和 `detail`。配置不完整或策略阻止执行时，HTTP 状态仍为 200，但 `status` 为 `degraded`，不可将 200 当作可评价。

`GET /api/v1/readiness` 同样要求内部令牌，返回 `evaluation_available`、`reason`、provider/model 标识和 `execution_policy`。它只检查配置与策略，不上传图片、不创建评价任务、不请求 provider。

- `isolated-stub-only` 为默认策略，仅允许本地确定性 `stub`。
- `configured-provider` 为显式生产策略，还需密钥、模型、内部令牌和视觉支持都具备，才会报告 `evaluation_available: true`。
- 未知策略、被策略禁止或配置缺失均关闭可用性。源码中的 `deepseek` 分支当前标记为非视觉能力；这是现有代码行为，不是外部模型能力的长期保证。

## 配置和运行

服务读取进程环境；开发时 `python-dotenv` 可加载 `.env`。R25 由托管的环境包装器提供私有配置，并以 `python -B -m uvicorn app.main:app --host 127.0.0.1 --port 19085` 启动，Python 导入路径指向本目录。

主要配置项为 `LLM_PROVIDER`、`LLM_API_KEY`（或 provider 对应的环境变量）、`LLM_BASE_URL`、`LLM_MODEL`、`AURALINK_CRITIC_INTERNAL_TOKEN` 与 `AURALINK_CRITIC_EXECUTION_POLICY`。这些值由运行环境管理，不写入源码、测试夹具或 Actions 产物。新增 provider 需另行验证图片输入和结构化输出契约。

在仓库根目录执行 `python -m pytest -q tests/test_critic_contract.py` 可验证鉴权、策略、图片错误和两种方案的九步离线流程。测试输出明确属于确定性 stub，不代表实际模型的艺术评价质量或生产网络验证。

发布与回滚由 Deploy 的 R25 控制负责，详见 [R25 发布边界](../docs/r25-ai-services.md)。
