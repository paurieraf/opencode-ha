"""Constants for the OpenCode integration."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

DOMAIN: Final = "opencode_conversation"

INTEGRATION_VERSION: Final = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)["version"]

BASE_URL: Final = "https://opencode.ai/zen/go/v1"

CONF_MODEL: Final = "model"
CONF_CUSTOM_MODEL: Final = "custom_model"
CONF_MAX_TOKENS: Final = "max_tokens"
CONF_API_FAMILY: Final = "api_family"
CONF_THINKING: Final = "thinking"
CONF_PROVIDER: Final = "provider"
CONF_BASE_URL: Final = "base_url"

DEFAULT_MODEL: Final = "deepseek-v4.1-flash"
DEFAULT_MAX_TOKENS: Final = 1024
DEFAULT_CONVERSATION_NAME: Final = "OpenCode Assistant"
DEFAULT_AI_TASK_NAME: Final = "OpenCode AI Task"

# API wire families served by the OpenCode Go gateway.
FAMILY_AUTO: Final = "auto"
FAMILY_CHAT: Final = "chat"
FAMILY_MESSAGES: Final = "messages"
FAMILY_RESPONSES: Final = "responses"

FAMILY_OPTIONS: Final = (FAMILY_AUTO, FAMILY_CHAT, FAMILY_MESSAGES, FAMILY_RESPONSES)

# Thinking control for Chat Completions models that accept the
# ``thinking: {"type": ...}`` request field (DeepSeek, GLM). "default" sends
# nothing and leaves the model's own behaviour untouched.
THINKING_DEFAULT: Final = "default"
THINKING_ENABLED: Final = "enabled"
THINKING_DISABLED: Final = "disabled"

THINKING_OPTIONS: Final = (THINKING_DEFAULT, THINKING_ENABLED, THINKING_DISABLED)

# Certified model -> wire family mapping.
#
# Source: OpenCode Go docs endpoint table (https://opencode.ai/v2/docs/console/go),
# cross-checked against the live API on 2026-10-09 (see docs/research.md,
# "Live verification results"). Models not listed here are hidden from the
# default picker but can be used via the custom model field with an explicit
# API family override. Keep this map in sync with the docs table on releases.
CERTIFIED_MODELS: Final[dict[str, str]] = {
    # OpenAI Chat Completions family
    "deepseek-v4.1-flash": FAMILY_CHAT,
    "deepseek-v4-pro": FAMILY_CHAT,
    "deepseek-v4-flash": FAMILY_CHAT,
    "deepseek-v4-flash-vision-exp": FAMILY_CHAT,
    "glm-5.3-flash": FAMILY_CHAT,
    "glm-5.3": FAMILY_CHAT,
    "glm-5.2": FAMILY_CHAT,
    "glm-5.1": FAMILY_CHAT,
    "hy3": FAMILY_CHAT,
    "hy4-preview": FAMILY_CHAT,
    "kimi-k2.6": FAMILY_CHAT,
    "kimi-k2.7-code": FAMILY_CHAT,
    "kimi-k3": FAMILY_CHAT,
    "longcat-2.0": FAMILY_CHAT,
    "longcat-2.5-preview-free": FAMILY_CHAT,
    "mimo-v2.5": FAMILY_CHAT,
    "mimo-v2.5-pro": FAMILY_CHAT,
    "mimo-v2.6-flash": FAMILY_CHAT,
    "mimo-v2.6-pro": FAMILY_CHAT,
    "space-bunny": FAMILY_CHAT,
    "step-5-preview-free": FAMILY_CHAT,
    # Anthropic Messages family
    "claude-haiku-5-5": FAMILY_MESSAGES,
    "minimax-m2.5": FAMILY_MESSAGES,
    "minimax-m2.7": FAMILY_MESSAGES,
    "minimax-m3": FAMILY_MESSAGES,
    "qwen3.6-plus": FAMILY_MESSAGES,
    "qwen3.7-max": FAMILY_MESSAGES,
    "qwen3.7-plus": FAMILY_MESSAGES,
    "qwen3.8-flash": FAMILY_MESSAGES,
    "qwen3.8-max": FAMILY_MESSAGES,
    # OpenAI Responses family
    "gpt-5.6-luna": FAMILY_RESPONSES,
    "gpt-6-luna": FAMILY_RESPONSES,
    "grok-4.6": FAMILY_RESPONSES,
    "grok-4.7": FAMILY_RESPONSES,
    "muse-spark-1.2-contributor": FAMILY_RESPONSES,
    "muse-spark-1.3-contributor": FAMILY_RESPONSES,
}

# Data handling for models that keep prompts or may use them for training,
# taken from the OpenCode Go privacy table. Shown next to the model in the
# setup and options flows.
MODEL_PRIVACY_NOTES: Final[dict[str, str]] = {
    "grok-4.6": "prompts kept 30 days",
    "grok-4.7": "prompts kept 30 days",
    "gpt-5.6-luna": "prompts kept 30 days",
    "gpt-6-luna": "prompts kept 30 days",
    "claude-haiku-5-5": "prompts kept 30 days",
    "muse-spark-1.2-contributor": "may train on prompts, limited regions",
    "muse-spark-1.3-contributor": "may train on prompts, limited regions",
}


@dataclass(frozen=True, kw_only=True)
class Provider:
    """An API provider the integration can talk to.

    Every provider speaks at least Chat Completions with Bearer auth. The
    key is validated against ``status_path``, whose response is also shown
    in diagnostics.
    """

    key: str
    name: str
    service_name: str
    base_url: str
    host: str
    status_path: str
    session_header: bool
    certified_models: Mapping[str, str]
    default_model: str
    privacy_notes: Mapping[str, str] = field(default_factory=dict)
    # Extra headers sent with every request.
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    # The user enters the base URL (a self-hosted server) instead of a fixed one.
    configurable_url: bool = False
    # Whether Home Assistant's LLM APIs (tools) can be offered to the model. A
    # provider that runs its own agent with its own tools sets this to False.
    hass_tools: bool = True
    # Turn reasoning on or off through ``model_options`` instead of the
    # Chat Completions ``thinking`` field.
    reasoning_option: bool = False
    # Subentry types created with the entry.
    initial_subentries: tuple[str, ...] = ("conversation", "ai_task_data")

    @property
    def default_conversation_name(self) -> str:
        """Return the default title of a conversation subentry."""
        return f"{self.name} Assistant"

    @property
    def default_ai_task_name(self) -> str:
        """Return the default title of an AI task subentry."""
        return f"{self.name} AI Task"


PROVIDER_OPENCODE_GO: Final = "opencode_go"
PROVIDER_DEEPSEEK: Final = "deepseek"
PROVIDER_HERMES: Final = "hermes"

# Source: DeepSeek API docs (https://api-docs.deepseek.com), checked against
# the live API on 2026-10-25. ``deepseek-flash`` is an alias that DeepSeek
# moves to its latest Flash release (DeepSeek-V4.1-Flash-1017 at that date).
DEEPSEEK_MODELS: Final[dict[str, str]] = {
    "deepseek-flash": FAMILY_CHAT,
    "deepseek-v4-pro": FAMILY_CHAT,
}

# From the DeepSeek privacy policy (updated 2026-02-10): prompts are kept while
# the account exists, stored in China and may be used for training unless the
# account owner opts out by email.
DEEPSEEK_PRIVACY_NOTES: Final[dict[str, str]] = {
    model: "prompts kept, may train on prompts" for model in DEEPSEEK_MODELS
}

PROVIDERS: Final[dict[str, Provider]] = {
    PROVIDER_OPENCODE_GO: Provider(
        key=PROVIDER_OPENCODE_GO,
        name="OpenCode",
        service_name="OpenCode Go",
        base_url=BASE_URL,
        host="opencode.ai",
        status_path="/usage",
        session_header=True,
        certified_models=CERTIFIED_MODELS,
        default_model=DEFAULT_MODEL,
        privacy_notes=MODEL_PRIVACY_NOTES,
    ),
    PROVIDER_DEEPSEEK: Provider(
        key=PROVIDER_DEEPSEEK,
        name="DeepSeek",
        service_name="DeepSeek",
        base_url="https://api.deepseek.com",
        host="api.deepseek.com",
        status_path="/user/balance",
        session_header=False,
        certified_models=DEEPSEEK_MODELS,
        default_model="deepseek-flash",
        privacy_notes=DEEPSEEK_PRIVACY_NOTES,
    ),
    # Hermes Agent API server (OpenAI-compatible), one profile per base URL,
    # e.g. ``http://10.1.1.114:8642/p/tono/v1``. Hermes runs its own agent and
    # tools server side, so no Home Assistant tools are sent; the model list is
    # the profile name served at ``/models``. Requests share one long-term
    # memory scope through ``X-Hermes-Session-Key``.
    PROVIDER_HERMES: Provider(
        key=PROVIDER_HERMES,
        name="Hermes",
        service_name="Hermes Agent",
        base_url="",
        host="",
        status_path="/models",
        session_header=False,
        certified_models={},
        default_model="",
        extra_headers={"X-Hermes-Session-Key": "home-assistant"},
        configurable_url=True,
        hass_tools=False,
        reasoning_option=True,
        initial_subentries=("conversation",),
    ),
}

PROVIDER_OPTIONS: Final = tuple(PROVIDERS)


def provider_for(key: str | None, base_url: str | None = None) -> Provider:
    """Return a provider, defaulting to OpenCode Go for entries made before providers.

    A provider with a configurable URL gets the entry's base URL.
    """
    provider = PROVIDERS.get(key or PROVIDER_OPENCODE_GO, PROVIDERS[PROVIDER_OPENCODE_GO])
    if provider.configurable_url and base_url:
        url = normalize_base_url(base_url)
        return replace(provider, base_url=url, host=urlparse(url).netloc)
    return provider


def provider_for_entry(data: Mapping[str, Any]) -> Provider:
    """Return the provider of a config entry's data."""
    return provider_for(data.get(CONF_PROVIDER), data.get(CONF_BASE_URL))


def normalize_base_url(url: str) -> str:
    """Return a base URL without surrounding spaces or a trailing slash."""
    return url.strip().rstrip("/")


def family_for(
    model: str,
    override: str | None = None,
    certified: Mapping[str, str] = CERTIFIED_MODELS,
) -> str:
    """Return the API wire family for a model.

    An explicit override wins; otherwise the provider's certified map is used,
    and unknown models fall back to Chat Completions.
    """
    if override and override != FAMILY_AUTO:
        return override
    return certified.get(model, FAMILY_CHAT)
