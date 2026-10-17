# Assistant S2 private execution contract

This is an isolated implementation candidate, not a production installation.
Core owns authorization, durable state, branches, citations, leases and replay.
The existing Critic FastAPI listener hosts `/internal/v1/assistant`; Guide,
Critic configuration and `/health` semantics are unchanged. No new supervisor.

Assistant is disabled without `AURALINK_ASSISTANT_CONFIG_FILE`. A malformed
configuration, missing secret or unsupported URL makes only Assistant return
503 `ASSISTANT_MISCONFIGURED`; it does not abort Critic startup. The config is
read once at startup. `AURALINK_ASSISTANT_INTERNAL_TOKEN_FILE` is an absolute
owner-only regular file containing a distinct internal token (16+ characters).
Every ready private route requires `Authorization: Bearer <internal token>`.
No user JWT, database credential or filesystem image path is sent to AI.

Example shape (all paths are placeholders, not deployable settings):

```json
{"mode":"enabled","allowLoopbackHttp":false,
 "models":[{"id":"selected-text","provider":"approved-provider","model":"approved-model",
  "label":"Configured model","baseUrl":"https://approved.example/v1",
  "keyFile":"/private/assistant-provider.key","capabilities":{"images":false,"streaming":true},
  "evidence":"CONFIGURED_NOT_VERIFIED"}],
 "ragflow":{"enabled":false},"limits":{"concurrency":2}}
```

The registry never reads PORT/QWEN_MODEL/DASHSCOPE_API_KEY/RAGFLOW_*.
Endpoints are server configuration only, HTTPS except explicitly enabled
numeric-loopback HTTP for isolated fixtures. Clients cannot supply URLs, key
files, credentials, dataset IDs, tools or extra request properties. Secrets
are never returned. Model capability labels are configuration declarations,
not verification of provider capabilities. Only JPEG/PNG image input is
supported, checked for format/MIME/bytes/pixels off the event loop. No WebP.

## First release: external RAG deferred

The approved first-release scope uses `"ragflow":{"enabled":false}`. Omit
`baseUrl`, `apiKeyFile` and `datasetIds`; they are not startup dependencies in
this profile. The model registry reports retrieval `NOT_CONFIGURED`, so Web
must not offer document retrieval or send it as enabled. The normal request
reports `DISABLED` with no document citations. Even an old client requesting
retrieval receives `NOT_CONFIGURED` without opening an external RAG request.
Do not populate this profile with mock document references or claim document
grounding. Core's authenticated artwork catalog is a separate source and
continues to use its public projection and revision checks.

This scope does not remove the future RAG adapter or loosen its enabled-mode
URL, secret and dataset checks. It does not change Guide/Critic configuration.
The S5 validation allowance is not ongoing production model-spend approval.

## Wire contract

* GET `/models` returns `{contractVersion:1,configSha256,models:[{id,providerId,
  modelId,label,configured,enabled,capabilities:{images,streaming,imageMimeTypes},
  evidence}],retrieval:{state},limits}`; the hash is SHA-256 of exact JSON bytes.
* GET `/readiness` returns `{contractVersion:1,state:"READY",configSha256}`.
* POST `/retrieval`: `{contractVersion:1,configSha256,enabled,query}`. Returns
  `{contractVersion:1,source:"RAGFLOW",state,citations}`. States DISABLED,
  NOT_CONFIGURED, AVAILABLE, EMPTY, FORBIDDEN, TIMEOUT, UNAVAILABLE are distinct.
  Only POST `/api/v1/retrieval` is called, with configured `dataset_ids`,
  `question`, bounded `top_k/page_size`. Expected RAGFlow envelope is
  `{code:0,data:{chunks:[{id,dataset_id,document_id,document_keyword,content,
  document_version?}]}}`. Missing identity or out-of-allowlist chunks refuse
  the complete result. No ingestion/training requests. No internal paths/URLs
  survive projection. Missing upstream document version remains null;
  contentSha256 binds received text instead of inventing a version.
* POST `/executions`: `{contractVersion:1,runId,attemptId,fence,configSha256,
  purpose:"CHAT"|"SUMMARY",modelKey,messages:[{role:"user"|"assistant",content,
  images:[{mimeType,dataBase64}]}],citations:[],retrievalStates:{},
  budgets:{totalMs?,maxOutputTokens?}}`.
* POST `/executions/{attemptId}/cancel` closes the active provider stream.

SSE events have JSON data: `delta {text}`, `usage {providerReported:{prompt_tokens,
completion_tokens,total_tokens},purpose}`, `choices {cards:[{id,title,options:
[{id,label}]}]}`, `done {finishReason,usageMayBeUnavailable:true}`, `error {code}`.
Only explicit done is success. EOF/malformed/oversized streams, idle/total
 timeout and transport failure emit error. Incremental UTF-8, CRLF and multiline
SSE data are supported; frame and total output bounded. No retries, redirects,
proxy-env inheritance or provider switching. Unknown provider outcomes may
still be billed; Core must never auto-replay a recorded dispatch intent.

The private stream also carries `heartbeat {kind:"provider_activity"}` after an
observed, valid non-answer provider delta (`reasoning_content` with nonempty
text or `role:"assistant"`). It contains no reasoning or answer text and is
emitted at most once per `min(1000ms, idleMs/3)`, only when a corresponding
provider frame arrives. SSE comments, empty deltas, silence and unknown fields
do not generate activity. Core validates the exact payload and consumes it only
for transport liveness: it is not a persisted answer/source, public event or
completion. The existing total deadline remains binding even while activity
continues. Deploy this additive private protocol with the matching Core client;
older Core clients do not implement this event and reject it. New Core remains
compatible with the old AI event set. A future coordinated release must prevent
generation against the new-AI/old-Core combination; an AI-first publication that
immediately admits generation cannot be reused without that compatibility gate.
This is a protocol constraint, not an executed or newly approved release plan.

OpenAI-compatible `/chat/completions` request uses explicit registry model,
stream=true, stream_options.include_usage, max_tokens and messages. Images are
bounded data URLs, never remotely fetched. Provider must finish with a valid
finish_reason and `[DONE]`; usage can remain unavailable. Only this compatible
wire adapter is implemented; provider-specific unsupported dialects are not
silently adapted or claimed tested.

For the exact registry pair `provider="dashscope", model="qwen3.8-flash"`,
server configuration may explicitly set `thinkingMode="disabled"`. The raw
HTTP body then includes top-level `enable_thinking:false`. This is supported
by the exact model's [thinking documentation](https://help.aliyun.com/zh/model-studio/deep-thinking)
and [Chat Completions contract](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/),
checked 2026-10-15. Its default is thinking-enabled; `max_tokens` limits the
answer, not reasoning. An implicit provider default is therefore unsuitable
as an assumed short-reasoning budget for this route's bounded chat window.
The optional setting defaults to `provider-default` (no extra wire parameter)
for backward compatibility. Other model/provider pairs reject an override;
no Flash-alias inference, arbitrary extra parameters or browser override is
accepted. Qwen3-VL, Guide and Critic keep their existing behavior.

The isolated accepted candidate opts out of thinking for this one route, without
changing the question, sources, max_tokens=4096, retries, or finite deadlines
(Core/AI 90s, acceptance harness 105s). It does not set thinking_budget,
reasoning_effort, stop, response_format or preserve_thinking; no reasoning is
stored/replayed as history. The authorized 2026-10-17 synthetic Flash text/source
case completed in 26.869 seconds and passed its original factual/source,
restoration and export criteria; one request, with no retries. This establishes
only that case, not general latency/accuracy, multi-turn or visual acceptance.
The earlier image-free VL source-grounding case passed without this parameter;
VL serialization is unchanged and no second VL call was made. Windows human
acceptance remains pending. Historical failed calls and their accounting are
preserved. Changing a startup config still requires a new digest binding, not
editing a previously approved record in place. No credentials or isolated
runtime configuration belong in this repository.

Process-lifetime duplicate attempt IDs return 409 even after completion. This
is supplementary to Core durable fencing, not cross-process exactly-once.
Capacity is bounded and refuses new attempts when full rather than evicting
replay protection. Core schedules CHAT before queued summaries; this layer
has separate bounded execution/retrieval slots, no pending queue. Browser
reconnection is Core-only and does not call this execution route again.

Choice JSON is optional fenced `assistant-choices` output, validated strictly;
invalid cards remain ordinary answer text with no repair-model call. Reference
text is labeled untrusted data; only supplied citation IDs may be displayed as
links after Core validation. Assistant has no database or record authority.
