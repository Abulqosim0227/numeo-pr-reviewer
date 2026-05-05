import os
import sys
from pathlib import Path

from .github_api import GitHubClient, GitHubError, parse_pr_url
from .llm import LLMError, review_pr
from .observability import RunLogger
from .prompts import build_user_prompt, system_prompt
from .schema import Decision, InlineComment, ReviewResult


def _candidate_reviewers() -> list[str]:
    raw = os.environ.get("NUMEO_REVIEWERS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _filter_inline_comments(
    comments: list[InlineComment], changed_files: set[str]
) -> tuple[list[InlineComment], list[InlineComment]]:
    kept, dropped = [], []
    for c in comments:
        (kept if c.path in changed_files else dropped).append(c)
    return kept, dropped


def _fold_comments_into_body(body: str, comments: list[InlineComment]) -> str:
    if not comments:
        return body
    lines = ["", "---", "**Inline notes (could not be attached as review comments):**", ""]
    for c in comments:
        lines.append(f"- `{c.path}:{c.line}` — {c.body}")
    return body + "\n".join(lines)


def _decision_to_event(decision: Decision) -> str:
    return "APPROVE" if decision == Decision.APPROVE else "REQUEST_CHANGES"


def run(pr_url: str, mode: str, dry_run: bool = False, runs_dir: Path = Path("runs")) -> int:
    try:
        owner, repo, number = parse_pr_url(pr_url)
    except GitHubError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    logger = RunLogger(pr_url, mode, runs_dir=runs_dir)

    try:
        with GitHubClient() as gh:
            pr = gh.get_pr(owner, repo, number)
            diff, was_truncated = gh.get_pr_diff(owner, repo, number)
            changed_files = gh.get_changed_files(owner, repo, number)

            sys_p = system_prompt(mode)
            usr_p = build_user_prompt(
                pr=pr,
                diff=diff,
                was_truncated=was_truncated,
                changed_files=changed_files,
                candidate_reviewers=_candidate_reviewers(),
            )

            result, raw, metrics = review_pr(sys_p, usr_p)
            logger.set_llm_call(sys_p, usr_p, raw, result, metrics)

            kept, dropped = _filter_inline_comments(result.inline_comments, set(changed_files))
            if dropped:
                logger.add_action(f"dropped {len(dropped)} inline comments with unknown paths")

            event = _decision_to_event(result.decision)
            body = result.summary
            if was_truncated:
                body = "Note: diff was truncated; review based on partial information.\n\n" + body
            if result.risk_notes:
                body += "\n\n**Risk notes:**\n" + "\n".join(f"- {r}" for r in result.risk_notes)

            if dry_run:
                logger.add_action(f"DRY RUN — would post {event} review")
                if event == "REQUEST_CHANGES" and result.suggested_reviewers:
                    logger.add_action(f"DRY RUN — would request {result.suggested_reviewers}")
            else:
                try:
                    gh.post_review(owner, repo, number, event, body, kept)
                    logger.add_action(f"posted review event={event} inline_comments={len(kept)}")
                except GitHubError as e:
                    err = str(e)
                    if "own pull request" in err:
                        folded = _fold_comments_into_body(
                            f"**Agent decision: {event}** (cannot self-review via API; posting as comment)\n\n"
                            + body,
                            kept,
                        )
                        gh.post_issue_comment(owner, repo, number, folded)
                        logger.add_action(
                            f"posted issue comment (self-PR fallback) event={event} inline={len(kept)}"
                        )
                    elif "422" in err and kept:
                        folded = _fold_comments_into_body(body, kept)
                        gh.post_review(owner, repo, number, event, folded, [])
                        logger.add_action(
                            f"posted review event={event} (inline rejected, folded into body)"
                        )
                    else:
                        raise

                if result.decision == Decision.ESCALATE and result.suggested_reviewers:
                    try:
                        gh.request_reviewers(owner, repo, number, result.suggested_reviewers)
                        logger.add_action(f"requested reviewers: {result.suggested_reviewers}")
                    except GitHubError as e:
                        logger.add_error(f"request_reviewers failed: {e}")

        log_path = logger.save()
        print(f"decision: {result.decision.value}")
        print(f"event:    {event}")
        print(f"inline:   {len(kept)} (dropped {len(dropped)})")
        print(f"tokens:   in={metrics.input_tokens} out={metrics.output_tokens}")
        print(f"latency:  {metrics.latency_ms} ms")
        print(f"log:      {log_path}")
        return 0

    except (GitHubError, LLMError) as e:
        logger.add_error(str(e))
        log_path = logger.save()
        print(f"error: {e}", file=sys.stderr)
        print(f"log:   {log_path}", file=sys.stderr)
        return 1
