import json
import os
import re
from typing import Protocol


def resolve_temperature(explicit=None):
    """Extraction default is 0. Kept at module level so the default is testable
    without instantiating a client (which requires the openai package)."""
    if explicit is not None:
        return float(explicit)
    return float(os.getenv("LLM_TEMPERATURE", "0"))


def resolve_seed(explicit=None):
    raw = os.getenv("LLM_SEED") if explicit is None else explicit
    return int(raw) if raw not in (None, "") else None

class ChatClient(Protocol):
    """Protocol for a chat client that can be stubbed in tests."""

    def chat(self, messages: list[dict[str, str]]) -> str:
        ...

class LLMClient:
    """Thin OpenAI-compatible chat client.

    TEMPERATURE. Extraction is a structured-output task with one intended
    answer, so it runs at temperature 0 by default. The API default is 1.0,
    which samples: three identical replays of the same conversation produced
    three differently-named — and on ambiguous input, differently-shaped —
    states. Sampling was doing work that nothing in the design asked for.

    Reply generation is conversational and is configured separately
    (LLM_REPLY_TEMPERATURE), because the argument for determinism there is not
    the same argument.

    SEED. Optional and best-effort (the provider does not guarantee it), but it
    makes prompt A/B comparison meaningful: with the seed pinned, a difference
    between two prompt versions is attributable to the prompt rather than to
    sampling. Leave unset for study runs.
    """

    def __init__(self, base_url=None, api_key=None, model=None,
                 temperature=None, seed=None, timeout=None):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "ollama")
        self.model = model or os.getenv("LLM_MODEL", "llama3.1")
        self.temperature = resolve_temperature(temperature)
        self.seed = resolve_seed(seed)
        # The SDK default is 10 minutes. A participant turn makes up to four
        # calls, and the turn lock is held throughout, so one hung request
        # freezes that conversation with no feedback. 45s is generous for these
        # prompts and fails fast enough that the retry in chat_json still helps.
        self.timeout = float(timeout if timeout is not None
                             else os.getenv("LLM_TIMEOUT", "45"))
        try:
            from openai import (
                OpenAI,  # lazy import so tests don't require openai installed
            )
            self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        except ImportError as e:
            raise ImportError(
                "openai package is required to use LLMClient without a stub. "
                "Install it with: pip install openai"
            ) from e

    def chat(self, messages):
        kwargs = {"model": self.model, "messages": messages,
                  "temperature": self.temperature, "timeout": self.timeout}
        if self.seed is not None:
            kwargs["seed"] = self.seed
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    def describe(self):
        """Decode settings, for recording alongside a session."""
        return {"model": self.model, "temperature": self.temperature,
                "seed": self.seed, "timeout": self.timeout}

    def chat_json(self, messages, max_retries=2):
        last_error = None
        for attempt in range(max_retries + 1):
            content = self.chat(messages)
            if not content or not content.strip():
                last_error = ValueError("Empty response")
                if attempt < max_retries:
                    print(f"[LLMClient] Empty response, retrying ({attempt + 1}/{max_retries})...")
                continue
            try:
                return self._extract_json(content)
            except json.JSONDecodeError as e:
                last_error = e
                if attempt < max_retries:
                    print(f"[LLMClient] JSON parse error, retrying ({attempt + 1}/{max_retries})...")
        raise ValueError(f"Failed after {max_retries + 1} attempts: {last_error}")

    def _extract_json(self, text):
        # Try ```json ... ``` block first
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            return json.loads(match.group(1).strip())
        # Use raw_decode to tolerate trailing content after the JSON value
        text = text.strip()
        try:
            obj, _ = json.JSONDecoder().raw_decode(text)
            return obj
        except json.JSONDecodeError:
            pass
        # Last resort: find the start of a JSON array or object and parse from there
        for ch in ("[", "{"):
            idx = text.find(ch)
            if idx != -1:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(text[idx:])
                    return obj
                except json.JSONDecodeError:
                    continue
        raise json.JSONDecodeError("No valid JSON found", text, 0)
