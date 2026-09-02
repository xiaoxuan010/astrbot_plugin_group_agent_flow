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
        resource = _load_json(ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json")
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
                    assert len(localized_item["labels"]) == len(item_schema["options"])


def test_renderer_options_and_labels_remain_aligned():
    """renderer 配置项是 string 下拉，options/labels/i18n 对齐。"""
    schema = _load_json(ROOT / "_conf_schema.json")
    renderer = schema["context"]["items"]["renderer"]
    assert renderer["type"] == "string"
    assert renderer["options"] == ["xml_delta", "line_messages"]
    assert renderer["default"] == "xml_delta"
    assert len(renderer["labels"]) == len(renderer["options"])

    for locale in LOCALES:
        resource = _load_json(ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json")
        localized = resource["config"]["context"]["renderer"]
        assert localized["description"]
        assert localized["hint"]
        assert len(localized["labels"]) == len(renderer["options"])


def test_context_window_uses_token_budget_and_cache_retention_settings():
    items = _load_json(ROOT / "_conf_schema.json")["context"]["items"]

    assert "max_messages_per_cycle" not in items
    assert items["max_context_tokens"]["type"] == "int"
    assert items["max_context_tokens"]["default"] == 8192
    assert "rotation_retention_ratio" not in items


def test_directed_cycle_interval_schema_contract():
    schema = _load_json(ROOT / "_conf_schema.json")
    item = schema["scheduling"]["items"]["direct_min_cycle_interval_seconds"]
    assert item["type"] == "float"
    assert item["default"] == 20.0

    for locale in LOCALES:
        resource = _load_json(ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json")
        localized = resource["config"]["scheduling"][
            "direct_min_cycle_interval_seconds"
        ]
        assert localized["description"]
