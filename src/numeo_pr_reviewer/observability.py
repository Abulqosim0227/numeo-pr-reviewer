import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ReviewResult, RunMetrics


class RunLogger:
    def __init__(self, pr_url: str, mode: str, runs_dir: Path = Path("runs")):
        self.runs_dir = runs_dir
        self.data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pr_url": pr_url,
            "mode": mode,
            "model": None,
            "system_prompt": None,
            "user_prompt": None,
            "raw_response": None,
            "parsed_result": None,
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": None,
            "actions_taken": [],
            "errors": [],
        }

    def set_llm_call(
        self,
        system_prompt: str,
        user_prompt: str,
        raw_response: str,
        parsed: ReviewResult,
        metrics: RunMetrics,
    ) -> None:
        self.data["system_prompt"] = system_prompt
        self.data["user_prompt"] = user_prompt
        self.data["raw_response"] = raw_response
        self.data["parsed_result"] = parsed.model_dump(mode="json")
        self.data["model"] = metrics.model
        self.data["input_tokens"] = metrics.input_tokens
        self.data["output_tokens"] = metrics.output_tokens
        self.data["latency_ms"] = metrics.latency_ms

    def add_action(self, action: str) -> None:
        self.data["actions_taken"].append(action)

    def add_error(self, error: str) -> None:
        self.data["errors"].append(error)

    def save(self) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = self.data["pr_url"].rstrip("/").replace("https://github.com/", "").replace("/", "_")
        path = self.runs_dir / f"{ts}_{slug}.json"
        path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
        return path
