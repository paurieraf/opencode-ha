"""Tests for the Hermes Agent API server provider."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType

from custom_components.opencode_conversation.api import OpenCodeClient
from custom_components.opencode_conversation.const import (
    DOMAIN,
    PROVIDER_HERMES,
    PROVIDERS,
    provider_for,
    provider_for_entry,
)

from .helpers import FakeClient

HERMES_URL = "http://10.1.1.114:8642/p/tono/v1"
HERMES_MODELS = {"object": "list", "data": [{"id": "tono", "object": "model"}]}

CHAT_EVENTS = [
    {"choices": [{"delta": {"role": "assistant", "content": "bé. i tu"}}]},
    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
]


async def create_hermes_entry(hass, aioclient_mock):
    """Create and set up a Hermes config entry through the config flow."""
    aioclient_mock.get(f"{HERMES_URL}/models", json=HERMES_MODELS)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"provider": "hermes", "api_key": "hermes-key", "base_url": f" {HERMES_URL}/ "},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"model": "tono"}
    )
    await hass.async_block_till_done()
    return hass.config_entries.async_entry_for_domain_unique_id(
        DOMAIN, result["result"].unique_id
    )


def test_provider_takes_the_entry_url():
    """The Hermes provider gets the entry's base URL; fixed providers ignore it."""
    hermes = provider_for(PROVIDER_HERMES, f"{HERMES_URL}/")
    assert hermes.base_url == HERMES_URL
    assert hermes.host == "10.1.1.114:8642"
    assert not hermes.hass_tools
    assert provider_for_entry({"provider": "hermes", "base_url": HERMES_URL}) == hermes
    assert provider_for("deepseek", HERMES_URL).base_url == "https://api.deepseek.com"
    assert PROVIDERS[PROVIDER_HERMES].base_url == ""


async def test_client_sends_memory_scope_and_validates_on_models(hass, aioclient_mock):
    """The Hermes client checks the key on /models and scopes memory per Home Assistant."""
    aioclient_mock.get(f"{HERMES_URL}/models", json=HERMES_MODELS)
    client = OpenCodeClient(hass, "hermes-key", provider_for(PROVIDER_HERMES, HERMES_URL))

    await client.async_validate_key()

    assert str(aioclient_mock.mock_calls[0][1]) == f"{HERMES_URL}/models"
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["X-Hermes-Session-Key"] == "home-assistant"
    assert headers["authorization"] == "Bearer hermes-key"
    assert "x-opencode-session" not in headers


async def test_stream_skips_tool_progress(hass, aioclient_mock):
    """Hermes tool progress frames (named events, chunks without choices) are skipped."""
    aioclient_mock.post(
        f"{HERMES_URL}/chat/completions",
        text=(
            "event: hermes.tool.progress\n"
            'data: {"tool": "GetLiveContext", "status": "running", "toolCallId": "c1"}\n\n'
            'data: {"choices": [{"delta": {"content": "fet"}}]}\n\n'
            "data: [DONE]\n\n"
        ),
        headers={"Content-Type": "text/event-stream"},
    )
    client = OpenCodeClient(hass, "hermes-key", provider_for(PROVIDER_HERMES, HERMES_URL))

    events = [event async for event in client.stream_chat_completions({"model": "tono"})]

    assert [e for e in events if "choices" in e] == [
        {"choices": [{"delta": {"content": "fet"}}]}
    ]


async def test_user_flow_requires_a_url(hass, aioclient_mock):
    """Hermes has no fixed URL, so the flow asks for one before calling anything."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "hermes", "api_key": "hermes-key"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base_url": "base_url_required"}
    assert aioclient_mock.call_count == 0


async def test_user_flow_creates_hermes_entry(hass, aioclient_mock):
    """The entry keeps the normalized URL and gets one conversation without reasoning."""
    entry = await create_hermes_entry(hass, aioclient_mock)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.title == "Hermes"
    assert entry.data == {"api_key": "hermes-key", "provider": "hermes", "base_url": HERMES_URL}
    subentries = list(entry.subentries.values())
    assert [s.subentry_type for s in subentries] == ["conversation"]
    assert subentries[0].title == "Hermes Assistant"
    assert subentries[0].data["model"] == "tono"
    assert subentries[0].data["thinking"] == "disabled"
    assert hass.states.get("conversation.hermes_assistant") is not None


async def test_conversation_turns_reasoning_off_without_hass_tools(hass, aioclient_mock):
    """Hermes gets reasoning off through model_options and never Home Assistant's tools."""
    entry = await create_hermes_entry(hass, aioclient_mock)
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_LLM_HASS_API: ["assist"]}
    )
    await hass.async_block_till_done()
    fake = FakeClient([CHAT_EVENTS])
    entry.runtime_data = fake

    result = await conversation.async_converse(
        hass, "com estàs?", None, Context(), "ca", agent_id="conversation.hermes_assistant"
    )

    assert result.response.speech["plain"]["speech"] == "bé. i tu"
    body = fake.calls[0]
    assert body["model"] == "tono"
    assert body["model_options"] == {"reasoning": {"enabled": False}}
    assert "thinking" not in body
    assert "tools" not in body


async def test_subentry_form_hides_hass_apis(hass, aioclient_mock):
    """A Hermes agent form offers the live profile model and no Home Assistant APIs."""
    entry = await create_hermes_entry(hass, aioclient_mock)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "conversation"),
        context={"source": config_entries.SOURCE_USER},
    )

    schema = result["data_schema"].schema
    assert CONF_LLM_HASS_API not in {str(key) for key in schema}
    assert [o["value"] for o in schema["model"].config["options"]] == ["tono"]
