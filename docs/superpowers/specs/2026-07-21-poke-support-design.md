# QQ poke support design

Date: 2026-07-21
Status: Approved by the user's direct implementation request

## Context

AstrBot converts a NapCat `notice_type=notify, sub_type=poke` event into a group event with
`Poke(id=target_id)`. The plugin currently persists the event as `text="[ComponentType.Poke]"`
and `{"type":"poke"}`. The model can see that a poke occurred, while the target and whether the
bot was poked remain ambiguous. The injected tool set also has no poke action.

## Decision

- Serialize an inbound `Poke` component as `{"type":"poke","target_id":"..."}`.
- Render its event text as `[戳一戳 target=<QQ>]`; the existing structured line already supplies
  the actor through `sender=<QQ>` and `name=<name>`.
- Mark the event as directed at the bot when the poke target equals `self_id`.
- Add `poke_user(user_id)` as an external action tool. A target is valid when its QQ appears as
  a sender, mention, or poke target at or before the frozen snapshot.
- Send through AstrBot's native `MessageChain([Poke(id=user_id)])`, preserving the existing
  guarded external-action, outcome, cursor, and terminal-loop behavior.

## Errors and compatibility

`poke_user` returns `{"error":"user_not_found_in_snapshot","user_id":"..."}` before any QQ
action when the target is absent from the frozen snapshot. Existing JSONL records and renderer
formats remain compatible; only new poke events gain explicit target metadata and semantic text.

## Tests

Cover inbound serialization/directed detection, tool schema exposure, snapshot-bounded target
validation, gateway Poke chain construction, successful external-action recording, and the full
quality gate.
