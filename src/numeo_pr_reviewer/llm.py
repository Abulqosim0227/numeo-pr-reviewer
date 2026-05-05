import json
import os
import re
import time

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from .schema import ReviewResult, RunMetrics

DEFAULT_MODEL = "deepseek/deepseek-chat"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 4096
TEMPERATURE = 0.0

CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(Exception):
    pass


def _extract_json(text: str) -> str:
    stripped = text.strip()
    stripped = CODE_FENCE_RE.sub("", stripped).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError("No JSON object found in response")
    return stripped[start : end + 1]


def _make_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMError("OPENROUTER_API_KEY not set")
    return OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/Abulqosim0227/numeo-pr-reviewer",
            "X-Title": "numeo-pr-reviewer",
        },
    )


def _call(client: OpenAI, model: str, system_prompt: str, messages: list[dict]) -> tuple[str, int, int]:
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            messages=[{"role": "system", "content": system_prompt}, *messages],
        )
    except OpenAIError as e:
        raise LLMError(f"OpenRouter API call failed: {e}") from e

    text = resp.choices[0].message.content or ""
    usage = resp.usage
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    return text, in_tok, out_tok


def review_pr(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> tuple[ReviewResult, str, RunMetrics]:
    model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    client = _make_client()
    start = time.perf_counter()

    raw, in_tok, out_tok = _call(
        client, model, system_prompt, [{"role": "user", "content": user_prompt}]
    )

    try:
        parsed = ReviewResult.model_validate_json(_extract_json(raw))
    except (LLMError, ValidationError, json.JSONDecodeError) as first_err:
        raw, in2, out2 = _call(
            client,
            model,
            system_prompt,
            [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "Your previous response was not valid JSON matching the schema. "
                        f"Error: {first_err}. Return only the JSON object, no prose, no fences."
                    ),
                },
            ],
        )
        in_tok += in2
        out_tok += out2
        try:
            parsed = ReviewResult.model_validate_json(_extract_json(raw))
        except (LLMError, ValidationError, json.JSONDecodeError) as second_err:
            raise LLMError(f"LLM returned invalid JSON twice: {second_err}") from second_err

    latency_ms = int((time.perf_counter() - start) * 1000)
    return parsed, raw, RunMetrics(
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        latency_ms=latency_ms,
    )
