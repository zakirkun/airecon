"""OpenAI Responses API provider for AIRecon.

Uses the new POST /v1/responses endpoint (the Responses API) which is the
recommended API for new OpenAI integrations as of 2025.

Key differences from Chat Completions:
- Endpoint:  POST /v1/responses  (not /v1/chat/completions)
- Input:     `input` list (array of typed items)  +  `instructions` (system prompt)
- Tools:     `{"type": "function", "name": ..., "parameters": ...}` (no "function" wrapper)
- Tool call output items: {"type": "function_call_output", "call_id": ..., "output": ...}
- Streaming: SSE events with `type` field (e.g. "response.output_text.delta",
             "response.function_call_arguments.delta", "response.completed")

This client exposes the exact same async interface as OllamaClient so AgentLoop
works unchanged regardless of which provider is configured.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

import httpx

from .config import get_config

logger = logging.getLogger("airecon.openai_provider")


# ---------------------------------------------------------------------------
# Wrapper objects that look like Ollama SDK objects to AgentLoop
# ---------------------------------------------------------------------------

class _OpenAIChunk:
    """Wraps a Responses API SSE event to look like an Ollama streaming chunk.

    AgentLoop reads:
      chunk.message.content       — text delta (str | None)
      chunk.message.tool_calls    — list of _OpenAIToolCall | None
      chunk.done                  — bool (True on final event)
    """

    class _Message:
        def __init__(self, content: str | None, tool_calls: list | None) -> None:
            self.content = content
            self.tool_calls = tool_calls

    def __init__(
        self,
        content: str | None = None,
        tool_calls: list | None = None,
        done: bool = False,
    ) -> None:
        self.message = self._Message(content=content, tool_calls=tool_calls)
        self.done = done


class _OpenAIToolCall:
    """Normalises a Responses API function_call item to the shape AgentLoop expects.

    Ollama SDK shape:
      tc.function.name       str
      tc.function.arguments  dict | str
      tc.id                  str | None
    """

    class _Function:
        def __init__(self, name: str, arguments: dict | str) -> None:
            self.name = name
            self.arguments = arguments

    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        try:
            parsed_args: dict | str = json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            parsed_args = arguments

        self.function = self._Function(name=name, arguments=parsed_args)
        self.id = call_id


# ---------------------------------------------------------------------------
# Message conversion helpers
# ---------------------------------------------------------------------------

def _convert_messages_to_responses_format(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Convert Chat-Completions-style messages to Responses API format.

    Returns:
        (input_items, instructions_str)

    The `system` role becomes `instructions` (a plain string passed separately).
    All other messages are converted to `EasyInputMessage` items.
    Tool call outputs are converted to `function_call_output` items.
    Assistant messages with tool_calls are split into `function_call` items.
    """
    instructions: str | None = None
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            # Accumulate system messages into instructions
            if instructions is None:
                instructions = content or ""
            else:
                instructions = f"{instructions}\n\n{content or ''}"
            continue

        if role == "tool":
            # Tool call result: {"role": "tool", "tool_call_id": ..., "content": ...}
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": content or "",
            })
            continue

        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                # Emit each tool call as a separate function_call item
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    if isinstance(args, dict):
                        args = json.dumps(args)
                    input_items.append({
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": args,
                    })
                # If there's also content text alongside tool calls, include it
                if content:
                    input_items.append({
                        "role": "assistant",
                        "content": content,
                    })
                continue
            # Plain assistant message
            input_items.append({
                "role": "assistant",
                "content": content or "",
            })
            continue

        # user / developer
        input_items.append({
            "role": role,
            "content": content or "",
        })

    return input_items, instructions


def _convert_tools_to_responses_format(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Convert Chat-Completions tool definitions to Responses API format.

    Chat Completions:
      {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}

    Responses API:
      {"type": "function", "name": ..., "description": ..., "parameters": ...}
    """
    if not tools:
        return None
    result = []
    for t in tools:
        if t.get("type") == "function":
            fn = t.get("function", {})
            converted: dict[str, Any] = {
                "type": "function",
                "name": fn.get("name", ""),
            }
            if fn.get("description"):
                converted["description"] = fn["description"]
            if fn.get("parameters"):
                converted["parameters"] = fn["parameters"]
            if fn.get("strict") is not None:
                converted["strict"] = fn["strict"]
            result.append(converted)
        else:
            # Pass through unknown tool types as-is
            result.append(t)
    return result if result else None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class OpenAIProvider:
    """Async client for the OpenAI Responses API (POST /v1/responses).

    Interface mirrors OllamaClient so it is a transparent drop-in for AgentLoop.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        cfg = get_config()

        self.model = model or cfg.openai_model
        _base = (base_url or cfg.openai_base_url).rstrip("/")
        # Resolve API key: config → AIRECON env var → standard OPENAI_API_KEY env var
        _key = (
            api_key
            or cfg.openai_api_key
            or os.environ.get("AIRECON_OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self._timeout = cfg.openai_timeout

        if not _key:
            logger.warning(
                "OpenAI API key not set. Set openai_api_key in config or "
                "AIRECON_OPENAI_API_KEY / OPENAI_API_KEY environment variable."
            )

        self._headers = {
            "Authorization": f"Bearer {_key}",
            "Content-Type": "application/json",
        }
        self._base_url = _base
        self._client = httpx.AsyncClient(
            base_url=_base,
            headers=self._headers,
            timeout=httpx.Timeout(
                connect=15.0, read=self._timeout, write=30.0, pool=15.0
            ),
        )

        # GPT-4o: no native "thinking" mode, but full function calling
        self._supports_thinking = False
        self._supports_native_tools = True

        logger.info(
            "Initialized OpenAI Responses API provider: base_url=%s, model=%s",
            _base,
            self.model,
        )

    # ── Capability properties (same as OllamaClient) ──────────────────────

    @property
    def supports_thinking(self) -> bool:
        return self._supports_thinking

    @property
    def supports_native_tools(self) -> bool:
        return self._supports_native_tools

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._client.aclose()

    async def unload_model(self) -> None:
        """No-op for OpenAI (model lives in the cloud)."""
        pass

    # ── Health check ──────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Return True if the OpenAI endpoint is reachable and the key is valid."""
        try:
            resp = await self._client.get("/models", timeout=10.0)
            return resp.status_code == 200
        except Exception as exc:  # nosec B110 - health check, best-effort
            logger.debug("OpenAI health check failed: %s", exc)
            return False

    # ── Non-streaming completion ──────────────────────────────────────────

    async def complete(
        self, messages: list[dict[str, Any]], max_retries: int = 3
    ) -> str:
        """Single non-streaming completion via POST /responses. Returns assistant text."""
        import asyncio

        cfg = get_config()
        input_items, instructions = _convert_messages_to_responses_format(messages)

        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "temperature": cfg.openai_temperature,
            "max_output_tokens": cfg.openai_max_tokens,
            "stream": False,
        }
        if instructions:
            payload["instructions"] = instructions

        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = await self._client.post("/responses", json=payload)
                resp.raise_for_status()
                data = resp.json()
                # Extract text from output items
                return _extract_text_from_response(data)
            except Exception as e:
                err_str = str(e).lower()
                is_transient = any(
                    k in err_str
                    for k in ("timeout", "connection", "reset", "eof", "network")
                )
                if is_transient and attempt < max_retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "Transient OpenAI error in complete() (attempt %d/%d), "
                        "retrying in %ds: %s",
                        attempt + 1, max_retries + 1, wait, e,
                    )
                    last_err = e
                    await asyncio.sleep(wait)
                    continue
                raise

        raise RuntimeError(
            f"OpenAI complete() failed after {max_retries + 1} attempts: {last_err}"
        )

    # ── Streaming chat ─────────────────────────────────────────────────────

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        think: bool = False,  # noqa: ARG002  — no native thinking in GPT-4o
        max_retries: int = 3,
    ) -> AsyncIterator[_OpenAIChunk]:
        """Streaming chat via POST /responses with stream=True.

        Yields _OpenAIChunk objects whose shape matches what AgentLoop reads
        from the Ollama SDK so the agent parsing code doesn't need to change.

        Responses API streaming events relevant here:
          response.output_text.delta          — text content delta
          response.function_call_arguments.delta — function args delta
          response.output_item.done           — output item completed (function_call)
          response.completed                  — final event with full response object
        """
        import asyncio

        cfg = get_config()
        input_items, instructions = _convert_messages_to_responses_format(messages)
        converted_tools = _convert_tools_to_responses_format(tools)

        temperature = cfg.openai_temperature
        max_tokens_val = cfg.openai_max_tokens
        if options:
            temperature = options.get("temperature", temperature)
            max_tokens_val = options.get("num_predict", max_tokens_val)

        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "temperature": temperature,
            "max_output_tokens": max_tokens_val,
            "stream": True,
        }
        if instructions:
            payload["instructions"] = instructions
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = "auto"

        last_err: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                async with self._client.stream(
                    "POST",
                    "/responses",
                    json=payload,
                    timeout=httpx.Timeout(
                        connect=15.0, read=None, write=30.0, pool=15.0
                    ),
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise httpx.HTTPStatusError(
                            f"OpenAI API error {resp.status_code}: {body.decode()[:500]}",
                            request=resp.request,
                            response=resp,
                        )

                    # State for accumulating per-item streams
                    # item_index → {call_id, name, arguments_so_far}
                    fn_accum: dict[int, dict] = {}
                    text_buffer: list[str] = []

                    async for raw_line in resp.aiter_lines():
                        raw_line = raw_line.strip()
                        if not raw_line or not raw_line.startswith("data:"):
                            continue

                        data_str = raw_line[5:].strip()
                        if data_str == "[DONE]":
                            # Flush any remaining pending function calls
                            if fn_accum:
                                yield _OpenAIChunk(
                                    content="".join(text_buffer) or None,
                                    tool_calls=_flush_fn_accum(fn_accum),
                                    done=True,
                                )
                            else:
                                yield _OpenAIChunk(
                                    content="".join(text_buffer) or None,
                                    done=True,
                                )
                            return

                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        event_type = event.get("type", "")

                        # ── Text delta ────────────────────────────────────
                        if event_type == "response.output_text.delta":
                            delta = event.get("delta", "")
                            if delta:
                                text_buffer.append(delta)
                                yield _OpenAIChunk(content=delta, done=False)

                        # ── Function call args delta ──────────────────────
                        elif event_type == "response.function_call_arguments.delta":
                            item_index = event.get("output_index", 0)
                            delta = event.get("delta", "")
                            if item_index not in fn_accum:
                                fn_accum[item_index] = {
                                    "call_id": "",
                                    "name": "",
                                    "arguments": "",
                                }
                            fn_accum[item_index]["arguments"] += delta

                        # ── Output item added (gives us call_id and name) ─
                        elif event_type == "response.output_item.added":
                            item = event.get("item", {})
                            if item.get("type") == "function_call":
                                idx = event.get("output_index", 0)
                                if idx not in fn_accum:
                                    fn_accum[idx] = {
                                        "call_id": "",
                                        "name": "",
                                        "arguments": "",
                                    }
                                fn_accum[idx]["call_id"] = item.get("call_id", "")
                                fn_accum[idx]["name"] = item.get("name", "")

                        # ── Output item done ──────────────────────────────
                        elif event_type == "response.output_item.done":
                            item = event.get("item", {})
                            if item.get("type") == "function_call":
                                # Ensure we have the final name/call_id from the
                                # completed item (more authoritative than added event)
                                idx = event.get("output_index", 0)
                                if idx not in fn_accum:
                                    fn_accum[idx] = {
                                        "call_id": "",
                                        "name": "",
                                        "arguments": "",
                                    }
                                fn_accum[idx]["call_id"] = item.get("call_id", fn_accum[idx]["call_id"])
                                fn_accum[idx]["name"] = item.get("name", fn_accum[idx]["name"])
                                if item.get("arguments"):
                                    fn_accum[idx]["arguments"] = item["arguments"]

                        # ── Response completed ────────────────────────────
                        elif event_type == "response.completed":
                            # Final event — flush accumulated function calls
                            if fn_accum:
                                yield _OpenAIChunk(
                                    content="".join(text_buffer) or None,
                                    tool_calls=_flush_fn_accum(fn_accum),
                                    done=True,
                                )
                            else:
                                yield _OpenAIChunk(
                                    content="".join(text_buffer) or None,
                                    done=True,
                                )
                            return

                        # ── Error event ───────────────────────────────────
                        elif event_type == "response.failed":
                            err_obj = event.get("response", {}).get("error", {})
                            raise RuntimeError(
                                f"OpenAI response failed: {err_obj.get('code')}: "
                                f"{err_obj.get('message')}"
                            )

                return  # successful stream — exit retry loop

            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
                if attempt < max_retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "Transient OpenAI stream error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, max_retries + 1, wait, e,
                    )
                    last_err = e
                    await asyncio.sleep(wait)
                    continue
                raise

            except httpx.HTTPStatusError:
                raise

            except Exception as e:
                err_str = str(e).lower()
                is_transient = any(
                    k in err_str
                    for k in ("timeout", "connection", "reset", "eof", "network")
                )
                if is_transient and attempt < max_retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "OpenAI stream error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, max_retries + 1, wait, e,
                    )
                    last_err = e
                    await asyncio.sleep(wait)
                    continue
                raise

        raise RuntimeError(
            f"OpenAI chat_stream failed after {max_retries + 1} attempts: {last_err}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flush_fn_accum(accum: dict[int, dict]) -> list[_OpenAIToolCall]:
    """Convert accumulated function-call items into _OpenAIToolCall objects."""
    result: list[_OpenAIToolCall] = []
    for idx in sorted(accum):
        entry = accum[idx]
        result.append(
            _OpenAIToolCall(
                call_id=entry["call_id"],
                name=entry["name"],
                arguments=entry["arguments"],
            )
        )
    return result


def _extract_text_from_response(data: dict[str, Any]) -> str:
    """Extract plain text from a non-streaming Responses API response object."""
    output = data.get("output", [])
    parts: list[str] = []
    for item in output:
        if item.get("type") == "message":
            for content_part in item.get("content", []):
                if content_part.get("type") == "output_text":
                    parts.append(content_part.get("text", ""))
        elif item.get("type") == "function_call":
            # Shouldn't happen in non-streaming complete(), but handle gracefully
            parts.append(f"[function_call: {item.get('name')}]")
    return "".join(parts)
