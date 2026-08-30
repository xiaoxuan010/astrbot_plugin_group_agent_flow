# Voice Tool Contracts

## Scenario: Attach snapshot voice and transcribe it for text-only Providers

### 1. Scope / Trigger

Apply this contract when adding or changing model tools that read `voice` components from the
plugin SQLite observation log, or when a Provider request should carry voice attachments. It keeps
raw QQ voice URLs inside the plugin record only, and gives every non-audio Provider a text
transcript path.

### 2. Signatures

- `ToolRuntime(..., context: Context | None = None)`
- `ToolRuntime.build_tool_set(event: AstrMessageEvent | None = None) -> ToolSet`
- `ToolRuntime._current_provider_supports_modality(event, modality) -> bool`
- `ToolRuntime._current_provider_supports_audio(event) -> bool`
- `ToolRuntime._message_voice_refs(record) -> list[str]`
- `ToolRuntime._model_visible_record(record) -> dict`
- Model tool: `get_voice_transcript(message_id: str) -> str`
- `GroupAgentFlowPlugin._current_provider_supports_audio(event) -> bool`
- `GroupAgentFlowPlugin._voice_refs(record) -> list[str]`
- `GroupAgentFlowPlugin._resolve_voice_attachments(records) -> tuple[AudioAttachment, ...]`
- `observation.AudioAttachment(seq, url, token_cost)`
- Core audio boundary: `MediaResolver(url, media_type="audio").as_path()`, `get_media_duration(path)`
- STT boundary: `Context.get_using_stt_provider(umo)`, `STTProvider.get_text(audio_url)`
- `ProviderRequest.audio_urls: list[str]`

### 3. Contracts

- Voice capability follows the same modality gate as images. `_current_provider_supports_modality`
  accepts only a current Provider whose `provider_config` is a dict whose `modalities` is a list
  containing the requested modality (`"audio"` or `"image"`).
- `get_voice_transcript` enters the tool set only when the current Provider does **not** declare
  `audio`. An audio-capable Provider instead receives raw voice via `req.audio_urls` and hides the
  transcript tool.
- `voice` components are stored by `event_codec.py` with an optional internal `components[].url`
  local download reference. That reference is never model-visible.
- `context_renderers._xml_component` renders a `voice` component as a bare `<voice/>` tag, never
  emitting its `url`. Mixed messages keep text, message ID, and sender metadata.
- `ToolRuntime._model_visible_record` projects `voice` components to `{"type": "voice"}` and strips
  `source_url` from every component. `get_message` and `search_chat_history` use this projection, so
  raw voice URLs never enter model-visible JSON output.
- `get_voice_transcript` resolves `message_id` through `_message_in_snapshot()` and accepts records
  only when `seq <= snapshot_seq`. It collects voice references in component order via
  `_message_voice_refs` and rejects messages with no voice components.
- The transcript request uses `Context.get_using_stt_provider(event.unified_msg_origin)`, then calls
  `STTProvider.get_text(voice_ref)` once per voice reference in order. Results are joined with newlines.
- `main.py` probes voice references with `MediaResolver(voice_ref, media_type="audio")` and
  `get_media_duration`. A positive millisecond duration is estimated as
  `token_cost = ceil(duration_ms / 1000 * 6.25)`.
- Only voice references that pass duration probing are attached, and only when the current Provider
  declares `audio`. Attachments are passed to `prepare_observation_records(..., audio_attachments=...)`
  and only those whose `seq` falls inside the budget-selected record set are kept.
- `req.audio_urls` receives only the ordered, budget-selected voice URLs. The estimated `token_cost`
  is added to the observation's rendered token budget before the budget-directed trimming runs.

### 4. Validation & Error Matrix

- Provider declares `audio` in `modalities` -> hide `get_voice_transcript`, attach voice to
  `req.audio_urls`.
- Provider does **not** declare `audio` -> expose `get_voice_transcript`, attach no voice.
- Provider lookup fails -> unknown capability; treated as non-audio (expose transcript tool).
- Missing `message_id` or post-snapshot record -> `message_not_found_in_snapshot`.
- Message with zero `voice` components -> `message_contains_no_voice`.
- No STT Provider configured or enabled -> `stt_unavailable`.
- STT `get_text` raises or returns empty transcript -> `stt_failed`.
- Voice download or duration probe fails -> skip that attachment; never exceed token budget and never
  abort the cycle. A log records a URL-free diagnostic.
- Text plus voice tokens exceed `max_context_tokens` -> existing trimming excludes older records.

### 5. Good / Base / Bad Cases

- Good: an `audio` Provider gets `req.audio_urls` with ordered, budget-selected voices while a
  text-only Provider gets the `get_voice_transcript` tool and no raw voice.
- Base: no STT Provider is configured, so a text-only Provider's transcript tool returns
  `stt_unavailable` and the model falls back to the `<voice/>` marker in context.
- Bad: rendering `<voice url="..."/>`, leaking voice URL through `get_message`, or attaching raw
  voice to a non-audio Provider.

### 6. Tests Required

- `tests/test_tools.py` asserts the tool set exposes `get_voice_transcript` for a non-audio Provider
  and hides it for an `audio` Provider.
- `tests/test_tools.py` asserts `get_voice_transcript` is snapshot-scoped and never returns a raw
  voice URL; `get_message` projects `voice` components to a bare marker.
- `tests/test_observation.py` asserts `audio_attachments` contribute token cost and that only
  budget-selected voice URLs survive into `audio_urls`.
- `tests/test_buffer_lifecycle.py` asserts `inject_snapshot` attaches only budget-selected audio and
  keeps raw voice URLs out of the rendered context.
- `tests/test_context_renderers.py` asserts `<voice/>` is rendered without any `url` while video and
  file rendering keep their prior behavior.
