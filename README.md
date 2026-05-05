# numeo-pr-reviewer

Autonomous GitHub pull-request review agent. Given a PR URL, it fetches the diff,
asks an LLM for a review, and posts real comments / approvals / reviewer assignments
back to GitHub.

Built for the Numeo AI Product Engineering Challenge.

## Demo

Live demo PR: <DEMO_PR_URL>

That PR was reviewed by the agent in both modes. The two reviews are visible on the
PR conversation tab. Run logs (full prompt, raw LLM response, tokens, latency) for
each run are in `runs/` and excerpted at the bottom of this README.

## Quick start

Requirements: Python 3.10+

```bash
git clone https://github.com/Abulqosim0227/numeo-pr-reviewer.git
cd numeo-pr-reviewer

python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
# edit .env: set GITHUB_TOKEN and ANTHROPIC_API_KEY
```

Run a review:

```bash
.venv/bin/numeo-review https://github.com/owner/repo/pull/42 --mode conservative
.venv/bin/numeo-review https://github.com/owner/repo/pull/42 --mode aggressive
.venv/bin/numeo-review https://github.com/owner/repo/pull/42 --mode aggressive --dry-run
```

`--dry-run` skips all GitHub writes; the run log is still produced.

## Configuration

| Variable            | Required | Notes                                                    |
|---------------------|----------|----------------------------------------------------------|
| `GITHUB_TOKEN`      | yes      | Fine-grained PAT. Scopes: Contents R/W, Pull requests R/W, Issues R/W on the target repo. |
| `ANTHROPIC_API_KEY` | yes      | Anthropic API key. Used to call Claude.                  |
| `NUMEO_REVIEWERS`   | no       | Comma-separated GitHub usernames the LLM may assign as reviewers when escalating. Empty = no assignment. |

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

For a 6-hour build, a single high-quality call from a strong reasoning model
beats an agent loop with weak self-correction. Claude Sonnet 4.6 with the diff
in context produces better-targeted comments than a chain of cheaper calls.
If the LLM returns malformed JSON it gets one retry with the error fed back,
then the run fails loud (no silent default review).

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

## Observability

Every run writes `runs/<UTC-timestamp>_<owner>_<repo>_pull_<n>.json` containing:

| Field            | Notes                                                  |
|------------------|--------------------------------------------------------|
| `timestamp`      | UTC ISO8601                                            |
| `pr_url`, `mode` | Run inputs                                             |
| `model`          | Model id used (e.g. `claude-sonnet-4-6`)               |
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
