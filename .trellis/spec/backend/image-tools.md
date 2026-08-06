# Image Tool Contracts

## Scenario: Read original images or captions from a frozen snapshot

### 1. Scope / Trigger

Apply this contract when adding or changing model tools that read image components from the
plugin JSONL observation log. It keeps original image tokens within explicitly multimodal
Providers and gives every other Provider a text-caption path.

### 2. Signatures

- `ToolRuntime(..., context: Context | None = None)`
- `ToolRuntime.build_tool_set(event: AstrMessageEvent | None = None) -> ToolSet`
- Model tool: `get_message_images(message_id: str) -> CallToolResult | str`
- Model tool: `get_image_captions(message_id: str) -> str`
- Core media boundary:
  `resolve_image_ref_to_base64_data(ref, strict=True) -> ResolvedImageData | None`
- Caption boundary:
  `Provider.text_chat(prompt=caption_prompt, image_urls=image_refs) -> LLMResponse`

### 3. Contracts

- Both tools resolve `message_id` through `_message_in_snapshot()` and accept records only when
  `seq <= snapshot_seq`.
- Image references come from non-empty `components[]` entries with `type == "image"` and retain
  component order. `event_codec.py` stores a raw OneBot `message[].data.url` as optional
  `components[].source_url`; both tools prefer it over the legacy `components[].url` local
  reference.
- `source_url` is only a tool-download reference. Context renderers use `components[].url`, so a
  signed remote URL never enters the chat-model prompt. `get_message` and
  `search_chat_history` apply the same model-visible projection and omit `source_url` from their
  JSON results.
- `event_codec.py` keeps the optional raw OneBot `message_seq` on the message record. When Core
  cannot resolve an image reference, `get_message_images` asks `QQActionGateway` for fresh URLs:
  `get_msg(message_id)` first, then `get_group_msg_history(group_id, message_seq, count=20)` for
  records carrying that sequence.
- Refresh failures preserve the existing `image_unavailable` result; gateway diagnostics and
  URLs remain outside model-visible tool output.
- `get_message_images` enters the tool set only when the current conversation Provider has a
  dictionary `provider_config` whose `modalities` is a list containing `"image"`; that tool set
  hides `get_image_captions`.
- Missing, empty, non-list, or image-free `modalities` hides `get_message_images` and exposes
  `get_image_captions`.
- Current Provider lookup failure is treated as unknown capability, hides `get_message_images`,
  and exposes `get_image_captions`.
- `get_image_captions` reads
  `provider_settings.default_image_caption_provider_id` and `image_caption_prompt` through
  `Context.get_config(umo=event.unified_msg_origin)`.
- `get_message_images` returns one `TextContent` metadata item followed by one `ImageContent`
  per image. Each image contains raw base64 data and the MIME type resolved by AstrBot Core.
- `get_image_captions` passes all images from one message to one dedicated Provider call and
  returns `{"message_id":"...","caption":"..."}`.
- `main.py` passes the current event at the direct request, `on_llm_request`, and
  `on_agent_begin` tool-set construction points.

### 4. Validation & Error Matrix

- Missing or post-snapshot message -> `message_not_found_in_snapshot`.
- Message with zero valid image components -> `message_contains_no_images`.
- Core media resolution failure -> `image_unavailable`.
- Missing caption context or `default_image_caption_provider_id` ->
  `image_caption_provider_unconfigured`.
- Caption configuration lookup failure -> `image_caption_failed`.
- Missing caption Provider, Provider exception, or empty caption -> `image_caption_failed`.
- Model-visible failures contain compact codes and message IDs where applicable. URLs, base64
  data, prompts, traceback text, and Provider diagnostics stay out of failure results.

### 5. Good/Base/Bad Cases

- Good: a Provider declaring `["text", "image", "tool_use"]` receives only
  `get_message_images`, whose raw image result contains Core-compatible `ImageContent`.
- Base: a text-only Provider receives `get_image_captions`, which uses the configured visual
  Provider and returns one merged caption.
- Bad: an absent `modalities` field exposes raw images or a future message bypasses
  `snapshot_seq`.

### 6. Tests Required

- Tool-set tests cover explicit image support, text-only, empty, missing, and non-list
  `modalities`; assert that each Provider receives exactly one image-reading tool.
- Event-codec tests assert raw OneBot image URLs remain aligned with image components. Raw-image
  tests assert `source_url` takes precedence over an expired local path, ordered resolver calls
  with `strict=True`, NapCat refresh after expiry, metadata count, MIME types, `ImageContent`,
  future-message rejection, no-image rejection, and compact resolution errors.
- History-tool tests assert that `source_url` remains available to image readers but never appears
  in model-visible message or history-search results.
- Gateway tests cover direct `get_msg` refresh and the `message_seq`-anchored group-history
  fallback.
- Caption tests assert the dedicated Provider ID, prompt, ordered `image_urls`, merged caption,
  unconfigured Provider error, Provider failure, and absence of URL or exception leakage.
- Main-hook tests assert all three tool-set construction paths pass the current event.
- Full-suite checks include pytest, Ruff, compileall, package import, and `git diff --check`.

### 7. Wrong vs Correct

Wrong:

```python
tool_set = runtime.build_tool_set()
```

This loses the current conversation Provider and can produce a static capability decision.

Correct:

```python
tool_set = runtime.build_tool_set(event)
```

Build each request's tool set from the current event: an explicit `"image"` modality adds
`get_message_images`; every other capability state adds `get_image_captions`.
