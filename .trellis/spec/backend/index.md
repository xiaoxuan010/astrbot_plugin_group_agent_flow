# Backend Development Guidelines

This repository is a single-package Python plugin for AstrBot. The runtime boundary is
AstrBot's event and Agent hook API; active pending state is stored in a plugin-owned
SQLite Buffer. Legacy JSONL/JSON files remain read-only compatibility material.

| Guide | Scope |
|---|---|
| [Directory Structure](./directory-structure.md) | Module ownership and dependency direction |
| [Persistence](./persistence-guidelines.md) | Event logs, state files, migrations, and concurrency |
| [Error Handling](./error-handling.md) | Tool errors, gateway failures, and protocol fallbacks |
| [Image Tools](./image-tools.md) | Snapshot image lookup, caption routing, and multimodal visibility |
| [Voice Tools](./voice-tools.md) | Snapshot voice attachment, STT transcript routing, and URL privacy |
| [Logging](./logging-guidelines.md) | AstrBot logger usage and diagnostic boundaries |
| [Quality](./quality-guidelines.md) | Tests, linting, compatibility, and review checks |

Read the topic relevant to the files being changed. Changes spanning event ingestion,
storage, scheduling, and tools should also use the cross-layer thinking guide.
