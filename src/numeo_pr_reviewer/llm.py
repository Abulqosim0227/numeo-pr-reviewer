import json
import os
import re
import time

from anthropic import Anthropic, AnthropicError
from pydantic import ValidationError

from .schema import ReviewResult, RunMetrics

DEFAULT_MODEL = "claude-sonnet-4-6"
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


def review_pr(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
) -> tuple[ReviewResult, str, RunMetrics]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY not set")

    client = Anthropic(api_key=api_key)
    start = time.perf_counter()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except AnthropicError as e:
        raise LLMError(f"Anthropic API call failed: {e}") from e

    raw = response.content[0].text if response.content else ""

    try:
        parsed = ReviewResult.model_validate_json(_extract_json(raw))
    except (LLMError, ValidationError, json.JSONDecodeError) as first_err:
        try:
            retry = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=system_prompt,
                messages=[
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
        except AnthropicError as e:
            raise LLMError(f"Anthropic API retry failed: {e}") from e
        raw = retry.content[0].text if retry.content else ""
        try:
            parsed = ReviewResult.model_validate_json(_extract_json(raw))
        except (LLMError, ValidationError, json.JSONDecodeError) as second_err:
            raise LLMError(f"LLM returned invalid JSON twice: {second_err}") from second_err
        response = retry

    latency_ms = int((time.perf_counter() - start) * 1000)
    metrics = RunMetrics(
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
    )
    return parsed, raw, metrics
