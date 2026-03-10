"""Configuration management for AIRecon proxy."""

from __future__ import annotations

import dataclasses
import os
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR_NAME = ".airecon"
CONFIG_FILENAME = "config.json"


_workspace_root_cache: Path | None = None


def get_workspace_root() -> Path:
    """Return workspace root = <CWD>/workspace/ captured at first call (startup).

    Using CWD lets users place workspaces wherever they run `airecon start`,
    making monitoring easy: the workspace folder appears right beside where
    the command was launched.  The path is cached after the first call so
    it stays consistent even if os.getcwd() ever changes later in the process.
    """
    global _workspace_root_cache
    if _workspace_root_cache is None:
        _workspace_root_cache = Path.cwd() / "workspace"
        _workspace_root_cache.mkdir(parents=True, exist_ok=True)
    return _workspace_root_cache


DEFAULT_CONFIG = {
    # Provider: "ollama" (default/local) or "openai" (cloud)
    "provider": "ollama",

    # OpenAI-specific settings (used when provider=="openai")
    "openai_api_key": "",
    "openai_model": "gpt-4o",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_timeout": 120.0,
    "openai_temperature": 0.15,
    "openai_max_tokens": 16384,

    # Ollama-specific settings (used when provider=="ollama")
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3.5:122b",
    "ollama_timeout": 1900.0,
    "ollama_num_ctx": 65536,
    "ollama_num_ctx_small": 32768,
    "ollama_temperature": 0.15,
    "ollama_num_predict": 16384,
    "ollama_enable_thinking": True,
    "ollama_supports_thinking": True,
    "ollama_supports_native_tools": True,
    "proxy_host": "127.0.0.1",
    "proxy_port": 3000,
    "command_timeout": 900.0,
    "docker_image": "airecon-sandbox",
    "docker_auto_build": True,
    "tool_response_role": "tool",
    "deep_recon_autostart": True,
    "agent_max_tool_iterations": 500,
    "agent_repeat_tool_call_limit": 2,
    "agent_missing_tool_retry_limit": 2,
    "agent_plan_revision_interval": 30,
    "agent_exploration_mode": True,
    "agent_exploration_intensity": 0.8,
    "agent_exploration_temperature": 0.35,
    "agent_stagnation_threshold": 2,
    "agent_tool_diversity_window": 8,
    "agent_max_same_tool_streak": 3,
    "allow_destructive_testing": False,
    "browser_page_load_delay": 1.0,
    "ollama_keep_alive": "30m",
    "searxng_url": "http://localhost:8080",
    "searxng_engines": "google,bing,duckduckgo,brave,google_news,github,stackoverflow",
    "vuln_similarity_threshold": 0.7,
}


@dataclass(frozen=True)
class Config:
    """Application configuration loaded from ~/.airecon/config.json."""

    # Provider selection: "ollama" | "openai"
    provider: str

    # OpenAI
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    openai_timeout: float
    openai_temperature: float
    openai_max_tokens: int

    # Ollama
    ollama_url: str
    ollama_model: str

    # Proxy server
    proxy_host: str
    proxy_port: int

    # Timeouts (seconds)
    ollama_timeout: float
    command_timeout: float

    # Ollama Model Options
    ollama_num_ctx: int
    ollama_num_ctx_small: int
    ollama_temperature: float
    ollama_num_predict: int
    ollama_enable_thinking: bool
    ollama_supports_thinking: bool
    ollama_supports_native_tools: bool

    # Docker sandbox
    docker_image: str
    docker_auto_build: bool

    # Tooling behavior
    tool_response_role: str

    # Deep recon behavior
    deep_recon_autostart: bool

    # Agent loop controls
    agent_max_tool_iterations: int
    agent_repeat_tool_call_limit: int
    agent_missing_tool_retry_limit: int
    agent_plan_revision_interval: int
    agent_exploration_mode: bool
    agent_exploration_intensity: float
    agent_exploration_temperature: float
    agent_stagnation_threshold: int
    agent_tool_diversity_window: int
    agent_max_same_tool_streak: int

    # Safety
    allow_destructive_testing: bool

    # Browser
    browser_page_load_delay: float

    # Ollama model keep_alive (how long to keep model in VRAM)
    ollama_keep_alive: str

    # SearXNG self-hosted search (leave empty to use DuckDuckGo fallback)
    searxng_url: str
    searxng_engines: str

    # Vulnerability deduplication threshold (0.0-1.0, default 0.7)
    vuln_similarity_threshold: float

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load config from specified path or default ~/.airecon/config.json."""
        if config_path:
            config_file = Path(config_path)
            # If explicit path given, it MUST exist (or we let it error/warn?)
            # Valid decision: If user provides path, we try to load it. If
            # missing, we error.
        else:
            home_dir = Path.home()
            config_dir = home_dir / APP_DIR_NAME
            config_file = config_dir / CONFIG_FILENAME

            # Ensure directory exists only for default path
            if not config_dir.exists():
                # print(f"DEBUG: Creating config directory at {config_dir}")
                config_dir.mkdir(parents=True, exist_ok=True)

        current_config = DEFAULT_CONFIG.copy()

        # Load or Create
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    user_config = json.load(f)
                    # Merge user config into defaults
                    current_config.update(user_config)
            except Exception as e:
                logger.error(
                    "Failed to load config from %s: %s. "
                    "Resetting to defaults and rewriting config file.",
                    config_file, e,
                )
                # Rewrite corrupt config with defaults so next startup is clean
                try:
                    with open(config_file, "w") as f:
                        json.dump(DEFAULT_CONFIG, f, indent=4)
                    logger.info("Config file reset to defaults at %s", config_file)
                except Exception as write_err:
                    logger.error("Could not rewrite config file: %s", write_err)
        else:
            # Only generate default if using the default path
            if config_path is None:
                logger.info(
                    f"No config found. Generating default config at {config_file}")
                try:
                    with open(config_file, "w") as f:
                        json.dump(DEFAULT_CONFIG, f, indent=4)
                except Exception as e:
                    logger.error(f"Failed to write default config: {e}")
            else:
                logger.warning(
                    f"Configuration file not found at {config_file}. Using default configuration settings.")

        # Override with Environment Variables (Optional, for temporary
        # overrides)
        for key in current_config:
            env_key = f"AIRECON_{key.upper()}"
            if env_key in os.environ:
                val = os.environ[env_key]
                default_val = DEFAULT_CONFIG.get(key)
                if isinstance(default_val, bool):
                    current_config[key] = val.lower() in ("true", "1", "yes")
                elif isinstance(default_val, int):
                    try:
                        current_config[key] = int(val)
                    except BaseException:
                        pass
                elif isinstance(default_val, float):
                    try:
                        current_config[key] = float(val)
                    except BaseException:
                        pass
                else:
                    current_config[key] = val

        return cls.load_with_defaults(current_config)

    @classmethod
    def load_with_defaults(cls, raw: dict) -> Config:
        """Construct Config from a raw dict safely.

        - Unknown keys (old/removed fields) are silently ignored.
        - Missing keys fall back to DEFAULT_CONFIG values.
        - Wrong-typed values are coerced to the expected type (e.g. "3000" → 3000).

        This prevents cryptic dataclass errors when users have outdated
        config files that contain fields no longer in the dataclass, or
        when new fields are added without a migration step.
        """
        known_fields = {f.name for f in dataclasses.fields(cls)}
        merged = {k: DEFAULT_CONFIG[k] for k in known_fields if k in DEFAULT_CONFIG}
        merged.update({k: v for k, v in raw.items() if k in known_fields})
        unknown = set(raw) - known_fields
        if unknown:
            logger.warning(
                "Config: ignoring unknown fields (possibly from an older config): %s",
                ", ".join(sorted(unknown)),
            )

        # Type coercion: ensure each value matches the type of its default.
        for key in list(merged):
            default_val = DEFAULT_CONFIG.get(key)
            if default_val is None:
                continue
            expected_type = type(default_val)
            val = merged[key]
            if not isinstance(val, expected_type):
                try:
                    if expected_type is bool:
                        if isinstance(val, str):
                            merged[key] = val.lower() in ("true", "1", "yes")
                        else:
                            merged[key] = bool(val)
                    else:
                        merged[key] = expected_type(val)
                    logger.warning(
                        "Config: coerced '%s' from %s to %s",
                        key, type(val).__name__, expected_type.__name__,
                    )
                except (ValueError, TypeError):
                    logger.warning(
                        "Config: could not coerce '%s' value %r to %s — using default %r",
                        key, val, expected_type.__name__, default_val,
                    )
                    merged[key] = default_val

        # Bounds validation: reset out-of-range values to defaults.
        _BOUNDS: dict[str, tuple[float | None, float | None]] = {
            "vuln_similarity_threshold": (0.0, 1.0),
            "ollama_timeout": (1.0, None),
            "openai_timeout": (1.0, None),
            "openai_max_tokens": (1, None),
            "command_timeout": (1.0, None),
            "agent_max_tool_iterations": (1, None),
            "agent_repeat_tool_call_limit": (1, None),
            "agent_missing_tool_retry_limit": (0, None),
            "agent_plan_revision_interval": (1, None),
            "agent_exploration_intensity": (0.0, 1.0),
            "agent_exploration_temperature": (0.0, 2.0),
            "agent_stagnation_threshold": (1, None),
            "agent_tool_diversity_window": (3, None),
            "agent_max_same_tool_streak": (1, None),
            "ollama_num_ctx": (1024, None),
            "ollama_num_ctx_small": (1024, None),
            "ollama_num_predict": (1, None),
        }
        for bkey, (lo, hi) in _BOUNDS.items():
            bval = merged.get(bkey)
            if bval is None:
                continue
            out_of_range = (lo is not None and bval < lo) or (hi is not None and bval > hi)
            if out_of_range:
                default_bval = DEFAULT_CONFIG[bkey]
                logger.warning(
                    "Config: '%s' value %r is out of allowed range [%s, %s] — using default %r",
                    bkey, bval, lo, hi, default_bval,
                )
                merged[bkey] = default_bval

        return cls(**merged)


# Singleton
_config: Config | None = None
_config_mtime: float = 0.0
_config_path: Path | None = None


def _get_config_path(config_path: str | Path | None = None) -> Path:
    """Resolve the config file path."""
    if config_path:
        return Path(config_path)
    return Path.home() / APP_DIR_NAME / CONFIG_FILENAME


def get_config(config_path: str | None = None) -> Config:
    """Get or create the global config instance.

    Auto-reloads if the config file has been modified since last load.
    """
    global _config, _config_mtime, _config_path

    if _config_path is None:
        _config_path = _get_config_path(config_path)

    # Check if config file was modified (hot-reload)
    if _config is not None:
        try:
            current_mtime = (
                _config_path.stat().st_mtime if _config_path.exists() else 0.0
            )
            if current_mtime > _config_mtime:
                logger.info(
                    f"Config file changed — reloading from {_config_path}")
                _config = Config.load(_config_path)
                _config_mtime = current_mtime
        except Exception:  # nosec B110 - keep existing config if stat fails
            pass

    if _config is None:
        _config = Config.load(config_path)
        try:
            _config_mtime = (
                _config_path.stat().st_mtime if _config_path.exists() else 0.0
            )
        except Exception:
            _config_mtime = 0.0

    return _config


def reload_config() -> Config:
    """Force reload config from disk. Returns the new config."""
    global _config, _config_mtime
    _config = None
    _config_mtime = 0.0
    return get_config()
