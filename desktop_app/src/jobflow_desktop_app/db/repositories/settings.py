from __future__ import annotations

from dataclasses import dataclass
import json
import os

from ..connection import Database


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str = ""
    model: str = "gpt-5"
    api_key_source: str = "direct"
    api_key_env_var: str = ""


class AppSettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_value(self, key: str, default: str = "") -> str:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        return str(row["value"] or default)

    def set_value(self, key: str, value: str) -> None:
        with self.database.session() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )

    def get_openai_settings(self) -> OpenAISettings:
        source = self._normalize_api_key_source(
            self.get_value("openai_api_key_source", "direct")
        )
        env_var = self._normalize_api_key_env_var(
            self.get_value("openai_api_key_env_var", "")
        )
        return OpenAISettings(
            api_key=self.get_value("openai_api_key", ""),
            model=self.get_value("openai_model", "gpt-5"),
            api_key_source=source,
            api_key_env_var=env_var,
        )

    def get_effective_openai_settings(self) -> OpenAISettings:
        stored = self.get_openai_settings()
        resolved_api_key = self.resolve_api_key(stored)
        stored_model = stored.model.strip()
        env_model = ""
        if not stored_model:
            env_model = self._first_env(
                "JOBFLOW_OPENAI_MODEL",
                "AZURE_OPENAI_MODEL",
                "AZURE_OPENAI_DEPLOYMENT",
            )
        return OpenAISettings(
            api_key=resolved_api_key,
            model=stored_model or env_model or "gpt-5",
            api_key_source=stored.api_key_source,
            api_key_env_var=stored.api_key_env_var,
        )

    def get_openai_base_url(self) -> str:
        direct = self._first_env("OPENAI_BASE_URL", "OPENAI_API_BASE")
        if direct:
            return direct

        azure_endpoint = self._first_env("AZURE_OPENAI_ENDPOINT")
        if not azure_endpoint:
            return ""
        normalized = azure_endpoint.rstrip("/")
        if normalized.endswith("/openai/v1"):
            return normalized
        if normalized.endswith("/openai"):
            return f"{normalized}/v1"
        if normalized.endswith("/v1"):
            return f"{normalized}/openai/v1"
        return f"{normalized}/openai/v1"

    def save_openai_settings(self, settings: OpenAISettings) -> None:
        source = self._normalize_api_key_source(settings.api_key_source)
        env_var = self._normalize_api_key_env_var(settings.api_key_env_var)
        self.set_value("openai_api_key", settings.api_key.strip())
        self.set_value("openai_model", settings.model.strip() or "gpt-5")
        self.set_value("openai_api_key_source", source)
        self.set_value("openai_api_key_env_var", env_var)

    def resolve_api_key(self, settings: OpenAISettings | None = None) -> str:
        active = settings or self.get_openai_settings()
        source = self._normalize_api_key_source(active.api_key_source)
        if source == "env":
            env_name = self._normalize_api_key_env_var(active.api_key_env_var)
            if not env_name:
                return ""
            return self._read_environment_variable(env_name)

        direct_key = active.api_key.strip()
        if direct_key:
            return direct_key
        return ""

    def list_api_key_environment_variables(self) -> list[str]:
        stored = self.get_openai_settings()
        candidates: list[str] = []
        seen: set[str] = set()
        process_env_by_upper = {name.upper(): name for name in os.environ.keys()}
        persisted_names = self._list_persisted_environment_variable_names()
        persisted_upper = {str(name).upper() for name in persisted_names}
        if os.name == "nt":
            source_names = sorted(persisted_names, key=lambda value: str(value).casefold())
        else:
            source_names = sorted(process_env_by_upper.values(), key=lambda value: str(value).casefold())

        def push(raw: str) -> None:
            name = self._normalize_api_key_env_var(raw)
            if not name:
                return
            if os.name == "nt" and name.upper() not in persisted_upper:
                return
            canonical = process_env_by_upper.get(name.upper(), name)
            if not self._read_environment_variable(canonical):
                return
            key = name.upper()
            if key in seen:
                return
            seen.add(key)
            candidates.append(canonical)

        for name in source_names:
            upper = name.upper()
            if (
                "KEY" not in upper
                and "TOKEN" not in upper
                and "SECRET" not in upper
            ):
                continue
            push(name)
        if stored.api_key_env_var and (
            os.name != "nt" or stored.api_key_env_var.upper() in persisted_upper
        ):
            canonical = process_env_by_upper.get(stored.api_key_env_var.upper())
            if canonical:
                push(canonical)
        return candidates

    def get_openai_model_catalog(self) -> list[str]:
        raw = self.get_value("openai_model_catalog", "[]")
        try:
            payload = json.loads(raw)
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        ordered: list[str] = []
        seen: set[str] = set()
        for item in payload:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(text)
        return ordered

    def save_openai_model_catalog(self, models: list[str]) -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for item in models:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(text)
        self.set_value("openai_model_catalog", json.dumps(ordered, ensure_ascii=False))

    def get_ui_language(self) -> str:
        language = self.get_value("ui_language", "zh").strip().lower()
        if language in {"en", "english"}:
            return "en"
        return "zh"

    def save_ui_language(self, language: str) -> None:
        normalized = "en" if str(language).strip().lower() in {"en", "english"} else "zh"
        self.set_value("ui_language", normalized)

    @staticmethod
    def _first_env(*names: str) -> str:
        for name in names:
            value = os.getenv(name, "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _normalize_api_key_source(raw: str) -> str:
        return "env" if str(raw or "").strip().lower() == "env" else "direct"

    @staticmethod
    def _normalize_api_key_env_var(raw: str) -> str:
        name = str(raw or "").strip()
        return name

    @staticmethod
    def _read_environment_variable(name: str) -> str:
        target = str(name or "").strip()
        if not target:
            return ""
        value = os.getenv(target, "").strip()
        if value:
            return value
        target_upper = target.upper()
        for env_name, env_value in os.environ.items():
            if env_name.upper() == target_upper:
                return str(env_value or "").strip()
        return ""

    @staticmethod
    def _list_persisted_environment_variable_names() -> set[str]:
        if os.name != "nt":
            return set()
        try:
            import winreg  # type: ignore
        except Exception:
            return set()

        locations = (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        )
        names: set[str] = set()
        for hive, subkey in locations:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    index = 0
                    while True:
                        try:
                            name, _value, _type = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        text = str(name or "").strip()
                        if text:
                            names.add(text)
                        index += 1
            except OSError:
                continue
        return names
