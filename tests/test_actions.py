from types import SimpleNamespace

import pytest
from astrbot.api import message_components as Comp

from qq_gateway import QQActionGateway


class FakeEvent:
    def __init__(self):
        self.bot = FakeBot()
        self.message_obj = SimpleNamespace(raw_message={"self_id": 7})
        self.sent = []

    async def send(self, chain):
        self.sent.append(chain)


class FakeBot:
    def __init__(self):
        self.actions = []

    async def call_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return {"status": "ok"}


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
    assert result["success"] is True


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
async def test_poke_user_builds_native_poke_chain():
    event = FakeEvent()
    gateway = QQActionGateway()

    result = await gateway.poke_user(event, user_id="10001")

    chain = event.sent[0].chain
    assert len(chain) == 1
    assert isinstance(chain[0], Comp.Poke)
    assert chain[0].target_id() == "10001"
    assert result == {
        "success": True,
        "action": "poke_user",
        "user_id": "10001",
    }
