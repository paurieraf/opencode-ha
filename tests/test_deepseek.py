"""Tests for the DeepSeek API provider."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.opencode_conversation.api import (
    OpenCodeAuthError,
    OpenCodeClient,
    OpenCodeInsufficientBalance,
)
from custom_components.opencode_conversation.const import (
    DOMAIN,
    PROVIDER_DEEPSEEK,
    PROVIDER_OPENCODE_GO,
    PROVIDERS,
    family_for,
    provider_for,
)
from custom_components.opencode_conversation.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .helpers import (
    DEEPSEEK_BALANCE,
    DEEPSEEK_BALANCE_URL,
    DEEPSEEK_MODELS_URL,
    DEEPSEEK_URL,
    USAGE_URL,
    FakeClient,
    create_deepseek_entry,
)

DEEPSEEK = PROVIDERS[PROVIDER_DEEPSEEK]

CHAT_EVENTS = [
    {"choices": [{"delta": {"role": "assistant", "content": "Bon dia"}}]},
    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
]


def test_provider_for_defaults_to_opencode_go():
    """Entries created before providers existed keep using OpenCode Go."""
    assert provider_for(None).key == PROVIDER_OPENCODE_GO
    assert provider_for("unknown").key == PROVIDER_OPENCODE_GO
    assert provider_for(PROVIDER_DEEPSEEK) is DEEPSEEK


def test_deepseek_models_use_chat_completions():
    """DeepSeek models resolve to Chat Completions through the provider map."""
    for model in DEEPSEEK.certified_models:
        assert family_for(model, None, DEEPSEEK.certified_models) == "chat"
    assert set(DEEPSEEK.privacy_notes) <= set(DEEPSEEK.certified_models)
    assert DEEPSEEK.default_model in DEEPSEEK.certified_models


async def test_client_targets_deepseek_without_session_header(hass, aioclient_mock):
    """The DeepSeek client validates against the balance endpoint, no session header."""
    aioclient_mock.get(DEEPSEEK_BALANCE_URL, json=DEEPSEEK_BALANCE)
    client = OpenCodeClient(hass, "sk-deepseek", DEEPSEEK)

    await client.async_validate_key()

    assert aioclient_mock.call_count == 1
    assert "x-opencode-session" not in client._base_headers
    assert client._base_headers["user-agent"].startswith("oc-ha/")


async def test_stream_has_no_session_header(hass, aioclient_mock):
    """Streaming requests to DeepSeek skip the OpenCode session header."""
    aioclient_mock.post(
        f"{DEEPSEEK_URL}/chat/completions",
        text='data: {"choices": [{"delta": {"content": "Hi"}}]}\n\ndata: [DONE]\n\n',
        headers={"Content-Type": "text/event-stream"},
    )
    client = OpenCodeClient(hass, "sk-deepseek", DEEPSEEK)
    events = [
        event
        async for event in client.stream_chat_completions(
            {"model": "deepseek-flash"}, session_id="conversation-1"
        )
    ]

    assert events == [{"choices": [{"delta": {"content": "Hi"}}]}]
    headers = aioclient_mock.mock_calls[0][3]
    assert "x-opencode-session" not in headers
    assert headers["authorization"] == "Bearer sk-deepseek"


async def test_insufficient_balance(hass, aioclient_mock):
    """A 402 from DeepSeek raises OpenCodeInsufficientBalance."""
    aioclient_mock.post(
        f"{DEEPSEEK_URL}/chat/completions",
        status=402,
        json={"error": {"message": "Insufficient Balance", "type": "unknown_error"}},
    )
    client = OpenCodeClient(hass, "sk-deepseek", DEEPSEEK)
    with pytest.raises(OpenCodeInsufficientBalance):
        async for _event in client.stream_chat_completions({"model": "deepseek-flash"}):
            pass


async def test_plain_text_auth_failure(hass, aioclient_mock):
    """DeepSeek answers a missing key with a plain text 401."""
    aioclient_mock.get(
        DEEPSEEK_BALANCE_URL, status=401, text="Authentication Fails (governor)"
    )
    client = OpenCodeClient(hass, "", DEEPSEEK)
    with pytest.raises(OpenCodeAuthError, match="governor"):
        await client.async_validate_key()


async def test_user_flow_creates_deepseek_entry(hass, aioclient_mock):
    """Choosing DeepSeek stores the provider and offers only DeepSeek models."""
    aioclient_mock.get(DEEPSEEK_BALANCE_URL, json=DEEPSEEK_BALANCE)
    aioclient_mock.get(
        DEEPSEEK_MODELS_URL,
        json={"object": "list", "data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "deepseek", "api_key": "sk-deepseek"}
    )
    assert result["step_id"] == "model"
    options = result["data_schema"].schema["model"].config["options"]
    assert [option["value"] for option in options] == ["deepseek-flash", "deepseek-v4-pro"]
    assert "may train on prompts" in options[0]["label"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"model": "deepseek-flash"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "DeepSeek"

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data == {"api_key": "sk-deepseek", "provider": "deepseek"}
    subentries = {s.subentry_type: s for s in entry.subentries.values()}
    assert subentries["conversation"].title == "DeepSeek Assistant"
    assert subentries["ai_task_data"].title == "DeepSeek AI Task"
    assert subentries["conversation"].data["model"] == "deepseek-flash"
    # The OpenCode usage endpoint is never called for a DeepSeek entry.
    assert not any(str(call[1]) == USAGE_URL for call in aioclient_mock.mock_calls)


async def test_deepseek_entry_sets_up_entities(hass, aioclient_mock):
    """A DeepSeek entry loads conversation and AI task entities."""
    entry = await create_deepseek_entry(hass, aioclient_mock)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("conversation.deepseek_assistant") is not None
    assert hass.states.get("ai_task.deepseek_ai_task") is not None


async def test_deepseek_subentry_defaults(hass, aioclient_mock):
    """New subentries of a DeepSeek entry default to the DeepSeek model and name."""
    entry = await create_deepseek_entry(hass, aioclient_mock)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "conversation"),
        context={"source": config_entries.SOURCE_USER},
    )
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema if hasattr(key, "default")}
    assert defaults["name"] == "DeepSeek Assistant"
    assert defaults["model"] == "deepseek-flash"
    options = schema["model"].config["options"]
    assert [option["value"] for option in options] == ["deepseek-flash", "deepseek-v4-pro"]


async def test_deepseek_conversation(hass, aioclient_mock):
    """The DeepSeek agent names its provider in the system prompt."""
    entry = await create_deepseek_entry(hass, aioclient_mock)
    fake = FakeClient([CHAT_EVENTS])
    entry.runtime_data = fake

    result = await conversation.async_converse(
        hass, "Hola", None, Context(), "ca", agent_id="conversation.deepseek_assistant"
    )

    assert result.response.speech["plain"]["speech"] == "Bon dia"
    system = fake.calls[0]["messages"][0]["content"]
    assert "running through DeepSeek as model deepseek-flash" in system


async def test_insufficient_balance_spoken_in_conversation_language(hass, aioclient_mock):
    """An empty balance is reported in the language of the conversation."""
    entry = await create_deepseek_entry(hass, aioclient_mock)
    entry.runtime_data = FakeClient(error=OpenCodeInsufficientBalance("no money", 402))

    result = await conversation.async_converse(
        hass, "Hola", None, Context(), "ca", agent_id="conversation.deepseek_assistant"
    )

    assert result.response.speech["plain"]["speech"] == (
        "El compte de DeepSeek s\'ha quedat sense saldo. "
        "Recarrega\'l per continuar fent servir l\'assistent."
    )


async def test_deepseek_reauth_validates_against_deepseek(hass, aioclient_mock):
    """Reauthentication checks the new key with the entry\'s own provider."""
    entry = await create_deepseek_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(DEEPSEEK_BALANCE_URL, json=DEEPSEEK_BALANCE)
    aioclient_mock.get(
        DEEPSEEK_MODELS_URL, json={"object": "list", "data": [{"id": "deepseek-flash"}]}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "sk-new"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data == {"api_key": "sk-new", "provider": "deepseek"}
    assert str(aioclient_mock.mock_calls[0][1]) == DEEPSEEK_BALANCE_URL


async def test_entry_without_provider_uses_opencode(hass, aioclient_mock):
    """An entry created before providers existed still talks to OpenCode Go."""
    aioclient_mock.get(USAGE_URL, json={"usage": {}})
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "old-key"}, title="OpenCode")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.provider.key == PROVIDER_OPENCODE_GO
    assert str(aioclient_mock.mock_calls[0][1]) == USAGE_URL


async def test_diagnostics_show_provider(hass, aioclient_mock):
    """Diagnostics name the provider, list its models and show the balance."""
    entry = await create_deepseek_entry(hass, aioclient_mock)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["provider"] == "deepseek"
    assert set(diagnostics["certified_models"]) == {"deepseek-flash", "deepseek-v4-pro"}
    assert diagnostics["usage"] == DEEPSEEK_BALANCE
    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"
