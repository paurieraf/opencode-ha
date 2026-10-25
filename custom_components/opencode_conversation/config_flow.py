"""Config flow for OpenCode."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any

import probatio
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LLM_HASS_API,
    CONF_NAME,
    CONF_PROMPT,
)
from homeassistant.core import callback
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import OpenCodeAuthError, OpenCodeClient, OpenCodeError, OpenCodeRateLimited
from .const import (
    CONF_API_FAMILY,
    CONF_BASE_URL,
    CONF_CUSTOM_MODEL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROVIDER,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DOMAIN,
    FAMILY_AUTO,
    FAMILY_OPTIONS,
    PROVIDER_DEEPSEEK,
    PROVIDER_HERMES,
    PROVIDER_OPENCODE_GO,
    PROVIDERS,
    THINKING_DEFAULT,
    THINKING_DISABLED,
    THINKING_ENABLED,
    THINKING_OPTIONS,
    Provider,
    normalize_base_url,
    provider_for,
    provider_for_entry,
)

VolDictType = dict[probatio.Marker, Any]

_FAMILY_LABELS = {
    FAMILY_AUTO: "Automatic",
    "chat": "Chat Completions (OpenAI-compatible)",
    "messages": "Messages (Anthropic-compatible)",
    "responses": "Responses (OpenAI)",
}

_PROVIDER_LABELS = {
    PROVIDER_OPENCODE_GO: "OpenCode Go",
    PROVIDER_DEEPSEEK: "DeepSeek API",
    PROVIDER_HERMES: "Hermes Agent (self-hosted)",
}

_THINKING_LABELS = {
    THINKING_DEFAULT: "Model default",
    THINKING_ENABLED: "Enabled",
    THINKING_DISABLED: "Disabled",
}


def _model_label(model: str, provider: Provider) -> str:
    """Return the model name with its data handling note, if it has one."""
    if note := provider.privacy_notes.get(model):
        return f"{model} ({note})"
    return model


def _model_options(models: list[str], provider: Provider) -> list[SelectOptionDict]:
    """Build model selector options."""
    return [
        SelectOptionDict(value=model, label=_model_label(model, provider))
        for model in models
    ]


def _provider_options() -> list[SelectOptionDict]:
    """Build provider selector options."""
    return [
        SelectOptionDict(value=value, label=_PROVIDER_LABELS[value]) for value in PROVIDERS
    ]


def _family_options() -> list[SelectOptionDict]:
    """Build API family selector options."""
    return [
        SelectOptionDict(value=value, label=_FAMILY_LABELS[value])
        for value in FAMILY_OPTIONS
    ]


def _thinking_options() -> list[SelectOptionDict]:
    """Build thinking selector options."""
    return [
        SelectOptionDict(value=value, label=_THINKING_LABELS[value])
        for value in THINKING_OPTIONS
    ]


def _api_key_schema() -> probatio.Schema:
    """Return the schema for an API key field."""
    return probatio.Schema(
        {
            probatio.Required(CONF_API_KEY): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


def _user_schema() -> probatio.Schema:
    """Return the schema for the first setup step."""
    return probatio.Schema(
        {
            probatio.Required(CONF_PROVIDER, default=PROVIDER_OPENCODE_GO): SelectSelector(
                SelectSelectorConfig(options=_provider_options())
            ),
            probatio.Required(CONF_API_KEY): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            probatio.Optional(CONF_BASE_URL): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
        }
    )


def _selectable_models(provider: Provider, live: list[str]) -> list[str]:
    """Return the models a provider offers: certified ones, or live ones if it certifies none."""
    certified = set(provider.certified_models)
    if not certified:
        return sorted(live)
    return sorted(certified & set(live)) or sorted(certified)


class _FormError(Exception):
    """A field error found before talking to the provider."""


def _api_key_fingerprint(api_key: str) -> str:
    """Return the stored identifier for an API key."""
    return sha256(api_key.encode()).hexdigest()


class OpenCodeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the OpenCode config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._api_key: str | None = None
        self._base_url: str | None = None
        self._provider: Provider = provider_for(None)
        self._available_models: list[str] = sorted(self._provider.certified_models)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            base_url = normalize_base_url(user_input.get(CONF_BASE_URL) or "")
            provider = provider_for(user_input.get(CONF_PROVIDER), base_url)
            client = OpenCodeClient(self.hass, api_key, provider)
            try:
                if provider.configurable_url and not base_url:
                    errors[CONF_BASE_URL] = "base_url_required"
                    raise _FormError
                await client.async_validate_key()
            except _FormError:
                pass
            except OpenCodeAuthError:
                errors["base"] = "invalid_auth"
            except OpenCodeRateLimited:
                errors["base"] = "usage_limit"
            except OpenCodeError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(_api_key_fingerprint(api_key))
                self._abort_if_unique_id_configured()
                self._api_key = api_key
                self._provider = provider
                self._base_url = base_url if provider.configurable_url else None
                try:
                    live = await client.async_list_models()
                except OpenCodeError:
                    live = []
                self._available_models = _selectable_models(provider, live)
                if not self._available_models:
                    errors["base"] = "cannot_connect"
                else:
                    return await self.async_step_model()

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the model selection step."""
        assert self._api_key is not None
        if user_input is not None:
            model = user_input[CONF_MODEL]
            provider = self._provider
            data = {CONF_API_KEY: self._api_key, CONF_PROVIDER: provider.key}
            if self._base_url:
                data[CONF_BASE_URL] = self._base_url
            conversation_data = {
                CONF_MODEL: model,
                CONF_PROMPT: "",
                CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
                CONF_API_FAMILY: FAMILY_AUTO,
            }
            if provider.reasoning_option:
                # A voice agent answers faster without reasoning.
                conversation_data[CONF_THINKING] = THINKING_DISABLED
            subentries = {
                "conversation": {
                    "subentry_type": "conversation",
                    "data": conversation_data,
                    "title": provider.default_conversation_name,
                    "unique_id": None,
                },
                "ai_task_data": {
                    "subentry_type": "ai_task_data",
                    "data": {
                        CONF_MODEL: model,
                        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
                        CONF_API_FAMILY: FAMILY_AUTO,
                    },
                    "title": provider.default_ai_task_name,
                    "unique_id": None,
                },
            }
            return self.async_create_entry(
                title=provider.name,
                data=data,
                subentries=[subentries[kind] for kind in provider.initial_subentries],
            )

        models = self._available_models
        default_model = self._provider.default_model or models[0]
        if default_model not in models:
            models = [default_model, *models]

        return self.async_show_form(
            step_id="model",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_MODEL, default=default_model): SelectSelector(
                        SelectSelectorConfig(options=_model_options(models, self._provider))
                    ),
                }
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the reauth confirmation step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            entry = self._get_reauth_entry()
            client = OpenCodeClient(
                self.hass, api_key, provider_for_entry(entry.data)
            )
            try:
                await client.async_validate_key()
            except OpenCodeAuthError:
                errors["base"] = "invalid_auth"
            except OpenCodeRateLimited:
                errors["base"] = "usage_limit"
            except OpenCodeError:
                errors["base"] = "cannot_connect"
            else:
                fingerprint = _api_key_fingerprint(api_key)
                if self._fingerprint_in_use(entry, fingerprint):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=fingerprint,
                    data_updates={CONF_API_KEY: api_key},
                )

        provider = provider_for_entry(self._get_reauth_entry().data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_api_key_schema(),
            errors=errors,
            description_placeholders={"name": provider.name},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            entry = self._get_reconfigure_entry()
            client = OpenCodeClient(
                self.hass, api_key, provider_for_entry(entry.data)
            )
            try:
                await client.async_validate_key()
            except OpenCodeAuthError:
                errors["base"] = "invalid_auth"
            except OpenCodeRateLimited:
                errors["base"] = "usage_limit"
            except OpenCodeError:
                errors["base"] = "cannot_connect"
            else:
                fingerprint = _api_key_fingerprint(api_key)
                if self._fingerprint_in_use(entry, fingerprint):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=fingerprint,
                    data_updates={CONF_API_KEY: api_key},
                )

        provider = provider_for_entry(self._get_reconfigure_entry().data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_api_key_schema(),
            errors=errors,
            description_placeholders={"name": provider.name},
        )

    def _fingerprint_in_use(self, entry: ConfigEntry, fingerprint: str) -> bool:
        """Return if another entry already uses this API key."""
        return any(
            other.entry_id != entry.entry_id and other.unique_id == fingerprint
            for other in self.hass.config_entries.async_entries(DOMAIN)
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            "conversation": OpenCodeSubentryFlowHandler,
            "ai_task_data": OpenCodeSubentryFlowHandler,
        }


class OpenCodeSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing OpenCode conversation and AI task subentries."""

    options: dict[str, Any]

    @property
    def _provider(self) -> Provider:
        """Return the provider of the parent entry."""
        return provider_for_entry(self._get_entry().data)

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == SOURCE_USER

    @property
    def _default_title(self) -> str:
        """Return the default title for this subentry type."""
        if self._subentry_type == "ai_task_data":
            return self._provider.default_ai_task_name
        return self._provider.default_conversation_name

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        if self._subentry_type == "ai_task_data":
            self.options = {
                CONF_MODEL: self._provider.default_model,
                CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
                CONF_API_FAMILY: FAMILY_AUTO,
            }
        else:
            self.options = {
                CONF_MODEL: self._provider.default_model,
                CONF_PROMPT: "",
                CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
                CONF_API_FAMILY: FAMILY_AUTO,
            }
            if self._provider.reasoning_option:
                self.options[CONF_THINKING] = THINKING_DISABLED
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of a subentry."""
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage the subentry options."""
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        options = self.options

        if user_input is not None:
            data = dict(user_input)
            name = data.pop(CONF_NAME, None)
            custom_model = str(data.pop(CONF_CUSTOM_MODEL, "") or "").strip()
            if custom_model:
                data[CONF_MODEL] = custom_model
            if self._subentry_type == "conversation":
                if not data.get(CONF_LLM_HASS_API):
                    data.pop(CONF_LLM_HASS_API, None)
            else:
                data.pop(CONF_LLM_HASS_API, None)
                data.pop(CONF_PROMPT, None)
            if self._is_new:
                return self.async_create_entry(
                    title=name or self._default_title,
                    data=data,
                )
            return self.async_update_and_abort(
                entry, self._get_reconfigure_subentry(), data=data
            )

        provider = self._provider
        models = await self._async_available_models(entry)
        default_model = provider.default_model or (models[0] if models else "")
        current_model = options.get(CONF_MODEL, default_model)
        # A custom model is shown in its own field, so clearing that field and
        # saving falls back to a model from the list instead of keeping it.
        if current_model in models:
            model_default, custom_default = current_model, ""
        else:
            model_default, custom_default = default_model, current_model

        step_schema: VolDictType = {}
        if self._is_new:
            step_schema[probatio.Required(CONF_NAME, default=self._default_title)] = (
                TextSelector()
            )
        step_schema[
            probatio.Required(CONF_MODEL, default=model_default)
        ] = SelectSelector(SelectSelectorConfig(options=_model_options(models, provider)))
        # suggested_value, not default: the frontend omits a cleared field, and a
        # default would put the old custom model back on submit.
        step_schema[
            probatio.Optional(
                CONF_CUSTOM_MODEL, description={"suggested_value": custom_default}
            )
        ] = TextSelector()
        if self._subentry_type == "conversation" and provider.hass_tools:
            hass_apis = [
                SelectOptionDict(label=api.name, value=api.id)
                for api in llm.async_get_apis(self.hass)
            ]
            step_schema[
                probatio.Optional(
                    CONF_LLM_HASS_API, default=options.get(CONF_LLM_HASS_API, [])
                )
            ] = SelectSelector(
                SelectSelectorConfig(options=hass_apis, multiple=True)
            )
        if self._subentry_type == "conversation":
            step_schema[
                probatio.Optional(
                    CONF_PROMPT,
                    description={"suggested_value": options.get(CONF_PROMPT, "")},
                )
            ] = TextSelector(TextSelectorConfig(multiline=True))
        step_schema[
            probatio.Required(
                CONF_MAX_TOKENS, default=options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
            )
        ] = NumberSelector(
            NumberSelectorConfig(min=16, max=32768, mode=NumberSelectorMode.BOX)
        )
        step_schema[
            probatio.Optional(
                CONF_API_FAMILY, default=options.get(CONF_API_FAMILY, FAMILY_AUTO)
            )
        ] = SelectSelector(SelectSelectorConfig(options=_family_options()))
        step_schema[
            probatio.Optional(
                CONF_THINKING, default=options.get(CONF_THINKING, THINKING_DEFAULT)
            )
        ] = SelectSelector(SelectSelectorConfig(options=_thinking_options()))

        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(step_schema),
        )

    async def _async_available_models(self, entry: ConfigEntry) -> list[str]:
        """Return selectable models for this entry."""
        provider = self._provider
        try:
            client: OpenCodeClient = entry.runtime_data
            live = await client.async_list_models()
        except OpenCodeError:
            live = []
        models = set(_selectable_models(provider, live))
        current = self.options.get(CONF_MODEL)
        if current in provider.certified_models or (
            not provider.certified_models and current
        ):
            models.add(current)
        if provider.default_model:
            models.add(provider.default_model)
        return sorted(models)
