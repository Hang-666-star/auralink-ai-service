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

OpenAI-compatible `/chat/completions` request uses explicit registry model,
stream=true, stream_options.include_usage, max_tokens and messages. Images are
bounded data URLs, never remotely fetched. Provider must finish with a valid
finish_reason and `[DONE]`; usage can remain unavailable. Only this compatible
wire adapter is implemented; provider-specific unsupported dialects are not
silently adapted or claimed tested.

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
