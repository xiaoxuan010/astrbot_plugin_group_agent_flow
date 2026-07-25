from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("zh-CN", "en-US")


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def test_plugin_i18n_covers_metadata_and_configuration_schema():
    schema = _load_json(ROOT / "_conf_schema.json")

    for locale in LOCALES:
        resource = _load_json(
            ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json"
        )
        assert set(resource["metadata"]) >= {"display_name", "desc"}
        assert resource["metadata"]["display_name"]
        assert resource["metadata"]["desc"]

        localized_config = resource["config"]
        for section_name, section_schema in schema.items():
            localized_section = localized_config[section_name]
            assert localized_section["description"]
            for item_name, item_schema in section_schema["items"].items():
                localized_item = localized_section[item_name]
                assert localized_item["description"]
                if "hint" in item_schema:
                    assert localized_item["hint"]
                if "labels" in item_schema:
                    assert len(localized_item["labels"]) == len(
                        item_schema["options"]
                    )


def test_renderer_values_and_default_remain_stable():
    renderer = _load_json(ROOT / "_conf_schema.json")["context"]["items"][
        "renderer"
    ]
    assert renderer["options"] == [
        "legacy_delta",
        "plain_lines",
        "native_messages",
        "xml_delta",
    ]
    assert renderer["default"] == "legacy_delta"


def test_context_window_uses_token_budget_and_cache_retention_settings():
    items = _load_json(ROOT / "_conf_schema.json")["context"]["items"]

    assert "max_messages_per_cycle" not in items
    assert items["max_context_tokens"]["type"] == "int"
    assert items["max_context_tokens"]["default"] == 8192
    assert items["rotation_retention_ratio"]["type"] == "float"
    assert items["rotation_retention_ratio"]["default"] == 0.5
    assert items["rotation_retention_ratio"]["slider"] == {
        "min": 0.1,
        "max": 0.9,
        "step": 0.05,
    }


def test_renderer_labels_describe_the_actual_message_structure():
    schema = _load_json(ROOT / "_conf_schema.json")
    renderer = schema["context"]["items"]["renderer"]
    zh = _load_json(ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json")
    en = _load_json(ROOT / ".astrbot-plugin" / "i18n" / "en-US.json")

    assert renderer["labels"] == [
        "Aggregated Delta Block (Dash-separated)",
        "Aggregated Delta Block (Line-separated)",
        "Dedicated User Block per Message",
        "XML Delta Block (Structured Components)",
    ]
    assert zh["config"]["context"]["renderer"]["labels"] == [
        "聚合增量块（短线分隔）",
        "聚合增量块（换行分隔）",
        "逐消息独占 User 块",
        "XML 增量块（结构化组件）",
    ]
    assert en["config"]["context"]["renderer"]["labels"] == renderer["labels"]
    assert "<group_messages_delta>" in zh["config"]["context"]["renderer"]["hint"]
    assert "role=user" in zh["config"]["context"]["renderer"]["hint"]
    assert "XML" in zh["config"]["context"]["renderer"]["hint"]
