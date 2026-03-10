"""Interactive setup wizard for AIRecon.

Run via:  airecon setup

Guides the user through selecting a provider (Ollama or OpenAI) and
all key configuration options, then writes ~/.airecon/config.json.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


# ── ANSI helpers ─────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    """Wrap text in an ANSI color code (auto-disabled when not a TTY)."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(t: str) -> str:
    return _c("1", t)


def _cyan(t: str) -> str:
    return _c("36", t)


def _green(t: str) -> str:
    return _c("32", t)


def _yellow(t: str) -> str:
    return _c("33", t)


def _red(t: str) -> str:
    return _c("31", t)


def _dim(t: str) -> str:
    return _c("2", t)


# ── Input helpers ─────────────────────────────────────────────────────────────

def _prompt(question: str, default: str = "") -> str:
    """Ask a question and return the answer (or the default on empty input)."""
    default_hint = f" [{_dim(default)}]" if default else ""
    try:
        raw = input(f"  {question}{default_hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return raw if raw else default


def _prompt_bool(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {question} [{_dim(hint)}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true")


def _menu(question: str, options: list[tuple[str, str]], default: int = 1) -> int:
    """Show a numbered menu, return 1-based selection index."""
    print(f"\n  {question}")
    for i, (label, desc) in enumerate(options, 1):
        marker = _green("▶") if i == default else " "
        print(f"    {marker} {_bold(str(i))}. {label}  {_dim(desc)}")
    while True:
        try:
            raw = input(f"\n  Choice [{_dim(str(default))}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not raw:
            return default
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        print(f"  {_red('!')} Please enter a number between 1 and {len(options)}.")


# ── Section printers ──────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n  {_cyan('─' * 60)}")
    print(f"  {_bold(_cyan(title))}")
    print(f"  {_cyan('─' * 60)}")


# ── Main wizard ───────────────────────────────────────────────────────────────

def run_wizard(config_path: str | None = None) -> None:
    """Run the interactive setup wizard and write the config file."""

    config_file = Path(config_path) if config_path else Path.home() / ".airecon" / "config.json"

    # ── Welcome ──────────────────────────────────────────────────────────────
    print()
    print(f"  {_bold(_cyan('▄▖▄▖▄▖'))}")
    print(f"  {_bold(_cyan('▌▌▐ ▙▘█▌▛▘▛▌▛▌'))}")
    print(f"  {_bold(_cyan('▛▌▟▖▌▌▙▖▙▖▙▌▌▌'))}")
    print()
    print(f"  {_bold('AIRecon Setup Wizard')}")
    print(f"  {_dim('This will create or overwrite: ' + str(config_file))}")
    print()

    # Load existing config as base so we don't lose unrelated settings
    existing: dict[str, Any] = {}
    if config_file.exists():
        try:
            with open(config_file) as f:
                existing = json.load(f)
            print(f"  {_yellow('⚠')}  Found existing config — your current values are shown as defaults.")
        except Exception:
            print(f"  {_red('!')} Existing config is unreadable, starting fresh.")

    cfg: dict[str, Any] = dict(existing)

    # ── Provider selection ───────────────────────────────────────────────────
    _section("1 · LLM Provider")
    current_provider = cfg.get("provider", "ollama")
    default_prov = 2 if current_provider == "openai" else 1
    prov_choice = _menu(
        "Which LLM provider do you want to use?",
        [
            ("Ollama", "local models — free, private, requires GPU/CPU"),
            ("OpenAI", "cloud API — GPT-4o, requires API key"),
        ],
        default=default_prov,
    )
    provider = "ollama" if prov_choice == 1 else "openai"
    cfg["provider"] = provider

    # ── Provider-specific settings ────────────────────────────────────────────
    if provider == "ollama":
        _section("2 · Ollama Settings")
        cfg["ollama_url"] = _prompt(
            "Ollama endpoint URL",
            cfg.get("ollama_url", "http://127.0.0.1:11434"),
        )
        cfg["ollama_model"] = _prompt(
            "Model name (run 'ollama list' to see available)",
            cfg.get("ollama_model", "qwen3.5:122b"),
        )
        cfg["ollama_timeout"] = float(
            _prompt("Request timeout (seconds)", str(cfg.get("ollama_timeout", 1900.0)))
        )
        ctx_choice = _menu(
            "Context window size",
            [
                ("32768  (32K tokens)", "moderate VRAM — 8-16 GB GPU"),
                ("65536  (64K tokens)", "high VRAM — 16+ GB GPU  [default]"),
                ("131072 (128K tokens)", "very high VRAM — 32+ GB GPU"),
            ],
            default=2,
        )
        ctx_map = {1: 32768, 2: 65536, 3: 131072}
        cfg["ollama_num_ctx"] = ctx_map[ctx_choice]
        cfg["ollama_num_ctx_small"] = cfg["ollama_num_ctx"] // 2

        thinking_default = cfg.get("ollama_enable_thinking", True)
        cfg["ollama_enable_thinking"] = _prompt_bool(
            "Enable extended reasoning / thinking mode (for qwen3, deepseek-r1)?",
            default=thinking_default,
        )
        cfg["ollama_supports_thinking"] = cfg["ollama_enable_thinking"]
        cfg["ollama_supports_native_tools"] = True

    else:
        _section("2 · OpenAI Settings")
        print(f"\n  {_yellow('⚠')}  Your API key will be stored in plain text in {config_file}.")
        print(f"  {_dim('Alternative: set OPENAI_API_KEY or AIRECON_OPENAI_API_KEY env var instead.')}")

        existing_key = cfg.get("openai_api_key", "")
        key_prompt = (
            f"OpenAI API key (leave blank to keep existing: {_dim('sk-...' + existing_key[-4:] if existing_key else 'none')})"
            if existing_key
            else "OpenAI API key (or set env var OPENAI_API_KEY to skip)"
        )
        entered_key = _prompt(key_prompt, "").strip()
        if entered_key:
            cfg["openai_api_key"] = entered_key
        elif not existing_key:
            cfg["openai_api_key"] = ""

        cfg["openai_model"] = _prompt(
            "Model",
            cfg.get("openai_model", "gpt-4o"),
        )
        cfg["openai_base_url"] = _prompt(
            "API base URL (change for Azure/proxy endpoints)",
            cfg.get("openai_base_url", "https://api.openai.com/v1"),
        )
        cfg["openai_timeout"] = float(
            _prompt("Request timeout (seconds)", str(cfg.get("openai_timeout", 120.0)))
        )
        cfg["openai_temperature"] = float(
            _prompt("Temperature (0.0 = deterministic)", str(cfg.get("openai_temperature", 0.15)))
        )
        cfg["openai_max_tokens"] = int(
            _prompt("Max tokens per response", str(cfg.get("openai_max_tokens", 16384)))
        )

    # ── Docker settings ───────────────────────────────────────────────────────
    _section("3 · Docker Sandbox")
    cfg["docker_auto_build"] = _prompt_bool(
        "Auto-build Docker image if missing?",
        default=cfg.get("docker_auto_build", True),
    )
    cfg["command_timeout"] = float(
        _prompt("Max tool execution time (seconds)", str(cfg.get("command_timeout", 900.0)))
    )

    # ── Safety ───────────────────────────────────────────────────────────────
    _section("4 · Safety Settings")
    print(f"  {_dim('Destructive testing enables aggressive fuzzing and payload injection.')}")
    print(f"  {_red('⚠')}  {_bold('Only enable on authorized targets.')}")
    cfg["allow_destructive_testing"] = _prompt_bool(
        "Allow destructive/aggressive testing?",
        default=cfg.get("allow_destructive_testing", False),
    )

    # ── Search ───────────────────────────────────────────────────────────────
    _section("5 · Search Engine")
    print(f"  {_dim('SearXNG provides full Google dork operator support.')}")
    print(f"  {_dim('Leave blank to use DuckDuckGo fallback (limited, rate-limited).')}")
    cfg["searxng_url"] = _prompt(
        "SearXNG URL (or leave blank for DuckDuckGo)",
        cfg.get("searxng_url", "http://localhost:8080"),
    )

    # ── Summary + confirm ────────────────────────────────────────────────────
    _section("6 · Summary")
    print(f"\n  {_bold('Provider:')}  {_cyan(cfg['provider'])}")
    if cfg["provider"] == "ollama":
        print(f"  {_bold('Ollama:')}    {cfg.get('ollama_url')}  |  model: {_green(cfg.get('ollama_model', ''))}")
    else:
        key_display = (
            _dim("sk-..." + cfg.get("openai_api_key", "")[-4:])
            if cfg.get("openai_api_key")
            else _dim("(env var)")
        )
        print(f"  {_bold('OpenAI:')}    {cfg.get('openai_base_url')}  |  model: {_green(cfg.get('openai_model', ''))}")
        print(f"  {_bold('API key:')}   {key_display}")
    print(f"  {_bold('Destructive:')} {'yes' if cfg.get('allow_destructive_testing') else 'no'}")
    print(f"  {_bold('Search:')}    {cfg.get('searxng_url') or _dim('DuckDuckGo fallback')}")
    print()

    confirm = _prompt_bool(f"Save configuration to {config_file}?", default=True)
    if not confirm:
        print(f"\n  {_yellow('Aborted — nothing was saved.')}\n")
        return

    # ── Write config ──────────────────────────────────────────────────────────
    config_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(config_file, "w") as f:
            json.dump(cfg, f, indent=4)
        print(f"\n  {_green('✓')} Configuration saved to {_bold(str(config_file))}")
        print(f"\n  Start AIRecon:  {_cyan('airecon start')}\n")
    except Exception as e:
        print(f"\n  {_red('!')} Failed to write config: {e}\n")
        sys.exit(1)
