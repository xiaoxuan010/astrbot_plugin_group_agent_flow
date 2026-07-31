from types import SimpleNamespace

import pytest

from qq_gateway import QQActionGateway


class FakeEvent:
    def __init__(self):
        self.bot = FakeBot()
        self.message_obj = SimpleNamespace(raw_message={"self_id": 7})
        self.sent = []

    async def send(self, chain):
        self.sent.append(chain)

    def get_group_id(self):
        return "1"

    def get_self_id(self):
        return "7"


class FakeBot:
    def __init__(self):
        self.actions = []

    async def call_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_send_message_returns_visible_action_payload():
    event = FakeEvent()
    gateway = QQActionGateway()

    result = await gateway.send_message(
        event,
        content="大家好",
        mentions=["10001"],
    )

    assert result == {
        "success": True,
        "action": "send_message",
        "content": "大家好",
        "mentions": ["10001"],
    }


@pytest.mark.asyncio
async def test_reply_message_builds_reply_mentions_and_text_chain():
    event = FakeEvent()
    gateway = QQActionGateway()

    result = await gateway.reply_message(
        event,
        message_id="12345",
        content="收到",
        mentions=["10001"],
    )

    chain = event.sent[0].chain
    assert [component.__class__.__name__ for component in chain] == [
        "Reply",
        "At",
        "Plain",
    ]
    assert str(chain[0].id) == "12345"
    assert str(chain[1].qq) == "10001"
    assert chain[2].text == "收到"
    assert result == {
        "success": True,
        "action": "reply_message",
        "message_id": "12345",
        "content": "收到",
        "mentions": ["10001"],
    }


@pytest.mark.asyncio
async def test_refresh_message_images_uses_fresh_url_from_exact_message():
    event = FakeEvent()

    async def call_action(action, **kwargs):
        event.bot.actions.append((action, kwargs))
        assert action == "get_msg"
        return {
            "message_id": 12345,
            "message": [
                {
                    "type": "image",
                    "data": {"url": "https://cdn.example.com/fresh.jpg"},
                }
            ],
        }

    event.bot.call_action = call_action

    refs = await QQActionGateway().refresh_message_images(
        event,
        message_id="12345",
        message_seq="77",
    )

    assert refs == ["https://cdn.example.com/fresh.jpg"]
    assert event.bot.actions == [
        ("get_msg", {"message_id": 12345, "self_id": 7})
    ]


@pytest.mark.asyncio
async def test_refresh_message_images_uses_group_history_when_exact_lookup_fails():
    event = FakeEvent()

    async def call_action(action, **kwargs):
        event.bot.actions.append((action, kwargs))
        if action == "get_msg":
            raise RuntimeError("message expired")
        assert action == "get_group_msg_history"
        return {
            "messages": [
                {
                    "message_id": "12345",
                    "message": [
                        {
                            "type": "image",
                            "data": {"url": "https://cdn.example.com/history.jpg"},
                        }
                    ],
                }
            ]
        }

    event.bot.call_action = call_action

    refs = await QQActionGateway().refresh_message_images(
        event,
        message_id="12345",
        message_seq="77",
    )

    assert refs == ["https://cdn.example.com/history.jpg"]
    assert event.bot.actions == [
        ("get_msg", {"message_id": 12345, "self_id": 7}),
        (
            "get_group_msg_history",
            {"group_id": 1, "message_seq": 77, "count": 20, "self_id": 7},
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reaction", "emoji_id"),
    [
        ("赞", "76"),
        ("爱心", "66"),
        ("笑哭", "182"),
        ("捂脸", "264"),
    ],
)
async def test_react_message_maps_semantic_reaction_to_napcat_id(reaction, emoji_id):
    event = FakeEvent()
    gateway = QQActionGateway()

    result = await gateway.react_message(
        event,
        message_id="12345",
        reaction=reaction,
    )

    assert event.bot.actions == [
        (
            "set_msg_emoji_like",
            {"message_id": "12345", "emoji_id": emoji_id, "set": True},
        )
    ]
    assert result == {
        "success": True,
        "action": "react_message",
        "message_id": "12345",
        "reaction": reaction,
    }


@pytest.mark.asyncio
async def test_react_message_rejects_unknown_semantic_reaction():
    event = FakeEvent()
    gateway = QQActionGateway()

    with pytest.raises(ValueError, match="unsupported reaction"):
        await gateway.react_message(
            event,
            message_id="12345",
            reaction="庆祝",
        )

    assert event.bot.actions == []


@pytest.mark.asyncio
async def test_poke_user_calls_napcat_group_poke_action():
    event = FakeEvent()
    gateway = QQActionGateway()

    result = await gateway.poke_user(event, user_id="10001")

    assert event.sent == []
    assert event.bot.actions == [
        (
            "group_poke",
            {
                "group_id": "1",
                "user_id": "10001",
                "self_id": 7,
            },
        )
    ]
    assert result == {
        "success": True,
        "action": "poke_user",
        "user_id": "10001",
    }
