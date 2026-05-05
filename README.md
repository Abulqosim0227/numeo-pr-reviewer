# numeo-pr-reviewer

Autonomous GitHub pull-request review agent. Given a PR URL, it fetches the diff,
asks an LLM for a review, and posts real comments / approvals / reviewer assignments
back to GitHub.

Built for the Numeo AI Product Engineering Challenge.

## Demo

Live demo PR: **https://github.com/Abulqosim0227/numeo-pr-reviewer-demo/pull/1**

That PR was reviewed by the agent in both modes. Both reviews are visible on the PR
conversation tab. Same diff, opposite decisions:

| Mode         | Decision         | Sample of the comment                                              |
|--------------|------------------|--------------------------------------------------------------------|
| aggressive   | APPROVE          | "Changes are clean and maintain existing behavior."                |
| conservative | REQUEST_CHANGES  | "This touches core user business logic..." (escalated to humans)   |

The demo PR is owned by the same GitHub account as the PAT, so GitHub blocks the
formal review API (you can't review your own PR). The agent detects this and falls
back to posting an issue comment that contains the same decision and body. See the
"Self-PR fallback" section below.

Run logs (full prompt, raw LLM response, tokens, latency) live in `runs/` after each
invocation.

## Quick start

Requirements: Python 3.10+

```bash
git clone https://github.com/Abulqosim0227/numeo-pr-reviewer.git
cd numeo-pr-reviewer

python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
# edit .env: set GITHUB_TOKEN and OPENROUTER_API_KEY (and optionally LLM_MODEL)
```

Run a review:

```bash
.venv/bin/numeo-review https://github.com/owner/repo/pull/42 --mode conservative
.venv/bin/numeo-review https://github.com/owner/repo/pull/42 --mode aggressive
.venv/bin/numeo-review https://github.com/owner/repo/pull/42 --mode aggressive --dry-run
```

`--dry-run` skips all GitHub writes; the run log is still produced.

## Configuration

| Variable             | Required | Notes                                                    |
|----------------------|----------|----------------------------------------------------------|
| `GITHUB_TOKEN`       | yes      | Fine-grained PAT. Scopes: Contents R/W, Pull requests R/W, Issues R/W on the target repo. |
| `OPENROUTER_API_KEY` | yes      | OpenRouter key. Used to call any model via OpenRouter's OpenAI-compatible API. |
| `LLM_MODEL`          | no       | OpenRouter model id. Defaults to `deepseek/deepseek-chat`. Examples: `anthropic/claude-sonnet-4.5`, `openai/gpt-4o`. |
| `NUMEO_REVIEWERS`    | no       | Comma-separated GitHub usernames the LLM may assign as reviewers when escalating. Empty = no assignment. |

## Modes

| Mode           | Default decision | Approves when                              | Escalates when                                |
|----------------|------------------|--------------------------------------------|-----------------------------------------------|
| `conservative` | escalate         | Changes are trivial (docs, formatting, test-only, dep bump within major) | Anything touches business logic, auth, db, errors, public API, concurrency, or cannot be verified safe from the diff |
| `aggressive`   | approve          | No demonstrable bug or security issue      | Clear bug, security issue, breaking API change without versioning, weakened tests, missing error handling on critical paths |

Conservative escalates by posting `REQUEST_CHANGES` (blocks merging) and assigning
reviewers from `NUMEO_REVIEWERS`. Aggressive approves by posting `APPROVE` and may
still leave inline style nits.

## Architecture

```
src/numeo_pr_reviewer/
  cli.py            Typer entry point. One command, two flags.
  agent.py          Orchestrator. Fetch PR -> call LLM -> log -> execute.
  github_api.py     httpx-based GitHub REST client. PR fetch, diff, review, reviewers.
  llm.py            Anthropic client. JSON-validated review with token + latency capture.
  prompts.py        Base system prompt + conservative/aggressive overlays.
  schema.py         Pydantic models. The LLM output contract.
  observability.py  RunLogger. One JSON file per run under runs/.
```

Single one-shot LLM call per run (no agent loops, no tool use). The model receives
PR metadata + the unified diff + the list of changed files and returns one JSON
object matching the schema in `schema.py`. The orchestrator validates the response
with Pydantic, filters out inline comments whose paths are not in the changed-file
list, then executes via the GitHub API.

### Why one-shot, not iterative

For a 6-hour build, a single high-quality call from a strong model beats an agent
loop with weak self-correction. The default model (`deepseek/deepseek-chat`) is
chosen for its strong code-review ability per dollar; `LLM_MODEL` lets you swap
to anything OpenRouter exposes (Claude, GPT-4o, Llama, etc.) without code changes.
If the LLM returns malformed JSON it gets one retry with the error fed back, then
the run fails loud (no silent default review).

### Truncation

PRs whose unified diff exceeds 5000 lines are truncated. The truncation flag is
passed into the prompt so the model knows context is incomplete; combined with
conservative mode this naturally escalates large PRs to humans. Aggressive mode
will still produce a review on the visible portion but flags the limitation in
the summary.

### 422 fallback for inline comments

GitHub rejects inline comments whose `line` does not fall on a line in the diff
hunk. When this happens the agent retries the review post with `comments=[]` and
folds the rejected comments into the markdown body so the feedback is not lost.

### Self-PR fallback

GitHub forbids reviewing a PR you authored (any review event — APPROVE,
REQUEST_CHANGES, COMMENT). When the PAT owner equals the PR author, the formal
reviews API returns 422 "Cannot approve your own pull request". The agent
catches this specific error and falls back to a plain issue comment that
contains the same decision and body. Real PR-review semantics are lost
(no formal approval status), but the feedback is still posted and the run
log records `posted issue comment (self-PR fallback)` so the substitution is
auditable.

## Observability

Every run writes `runs/<UTC-timestamp>_<owner>_<repo>_pull_<n>.json` containing:

| Field            | Notes                                                  |
|------------------|--------------------------------------------------------|
| `timestamp`      | UTC ISO8601                                            |
| `pr_url`, `mode` | Run inputs                                             |
| `model`          | Model id used (e.g. `deepseek/deepseek-chat`)          |
| `system_prompt`  | Full system prompt sent to the LLM                     |
| `user_prompt`    | Full user prompt: PR metadata + diff + changed files   |
| `raw_response`   | Raw LLM text before JSON parsing                       |
| `parsed_result`  | Validated `ReviewResult` as JSON                       |
| `input_tokens`   | From the Anthropic response                            |
| `output_tokens`  | From the Anthropic response                            |
| `latency_ms`     | Wall-clock time including any retry on bad JSON        |
| `actions_taken`  | Each GitHub call we made (or skipped on dry-run)       |
| `errors`         | Any non-fatal errors (e.g. reviewer assignment failed) |

`runs/` is gitignored. Logs stay local.

## Known limits

- The PAT must already have access to the target repo. The agent will not request access.
- Repos with `CODEOWNERS` are not yet inspected; reviewers come from `NUMEO_REVIEWERS` only.
- No retry for GitHub rate limits. A 403 with `X-RateLimit-Remaining: 0` will fail the run; rerun later.
- The LLM occasionally suggests an inline comment on a line not in the diff hunk. The 422 fallback recovers the feedback as body text but the comment is no longer line-anchored.
- One LLM call per run. Very large monorepo PRs (5000+ diff lines) are truncated rather than chunked.

## AI tools used during development

Built with **Claude Code** (Opus 4.7, 1M-context). The plan, file structure, and
every code file were written iteratively through that session. Prompts and code
were reviewed line by line before each commit; nothing was generated and merged
unread.

## License

MIT
