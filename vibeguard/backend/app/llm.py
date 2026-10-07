"""Single-method Gemini client with a prompt-hash disk cache (powers replay mode)."""
import hashlib
import os
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    pass


class LLM:
    def __init__(self, cache_dir: Path, replay: bool = False):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.replay = replay
        self.model = os.getenv("VIBEGUARD_MODEL", "gemini-2.5-flash")
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    def _cache_path(self, prompt: str) -> Path:
        h = hashlib.sha256(f"{self.model}\n{prompt}".encode()).hexdigest()[:24]
        return self.cache_dir / f"{h}.json"

    async def generate_json(self, prompt: str, schema: Type[T]) -> T:
        cache = self._cache_path(prompt)
        if self.replay:
            if not cache.exists():
                raise LLMUnavailable("replay mode: no cached response for this prompt")
            return schema.model_validate_json(cache.read_text())
        if not self.api_key:
            raise LLMUnavailable("GEMINI_API_KEY not set")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        resp = await client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.2,
            ),
        )
        out = schema.model_validate_json(resp.text)   # raises if the model broke the schema
        cache.write_text(out.model_dump_json())
        return out
