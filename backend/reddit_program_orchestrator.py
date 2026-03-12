"""
Reddit program orchestration runtime with constrained target discovery.
"""

from __future__ import annotations

import asyncio
import collections
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import quote

import httpx

from forensics import is_infra_error_text
from reddit_bot import REDDIT_HTTP_HEADERS, run_reddit_action
from reddit_growth_generation import RedditGrowthContentGenerator, get_writing_rule_snapshot
from reddit_subreddit_policies import (
    normalize_subreddit_name,
    subreddit_keywords,
    subreddit_policy_for,
    subreddit_profile_is_eligible,
    subreddit_requires_user_flair,
    subreddit_auto_user_flair_enabled,
    subreddit_blocked_warmup_stages,
    subreddit_minimum_comment_karma,
    profile_user_flair,
)
from reddit_profile_capabilities import fetch_public_reddit_profile_stats
from reddit_program_notifications import (
    RedditProgramNotificationService,
    build_program_email_body,
)
from reddit_program_store import RedditProgramStore, refresh_reddit_program_state
from reddit_session import RedditSession

logger = logging.getLogger("RedditProgramOrchestrator")


class RedditProgramAlreadyRunningError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: Optional[datetime] = None) -> str:
    dt = value or _utc_now()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _short_text(value: Optional[str], limit: int = 160) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else text[:limit] + "..."


def _subreddit_from_url(url: Optional[str]) -> Optional[str]:
    match = str(url or "").split("?", 1)[0]
    parts = [segment for segment in match.split("/") if segment]
    if len(parts) >= 2 and parts[0].lower() == "r":
        return str(parts[1]).strip().lstrip("r/").strip("/") or None
    return None


def _thread_json_url(url: str) -> str:
    clean = str(url or "").split("?", 1)[0].strip().rstrip("/")
    return f"{clean}/.json?raw_json=1&limit=20"


def _program_filters(program: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((program.get("spec") or {}).get("topic_constraints") or {}))


def _execution_policy(program: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((program.get("spec") or {}).get("execution_policy") or {}))


def _verification_contract(program: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((program.get("spec") or {}).get("verification_contract") or {}))


def _generation_config(program: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((program.get("spec") or {}).get("generation_config") or {}))


def _subreddit_policy(program: Dict[str, Any], subreddit: Optional[str]) -> Dict[str, Any]:
    return subreddit_policy_for(_program_filters(program).get("subreddit_policies") or [], subreddit)


def _notification_config(program: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((program.get("spec") or {}).get("notification_config") or {}))


def _realism_policy(program: Dict[str, Any]) -> Dict[str, Any]:
    policy = dict(((program.get("spec") or {}).get("realism_policy") or {}))
    return {
        "forbid_own_content_interactions": bool(policy.get("forbid_own_content_interactions", True)),
        "require_conversation_context": bool(policy.get("require_conversation_context", True)),
        "require_subreddit_style_match": bool(policy.get("require_subreddit_style_match", True)),
        "forbid_operator_language": bool(policy.get("forbid_operator_language", True)),
        "forbid_meta_testing_language": bool(policy.get("forbid_meta_testing_language", True)),
    }


def _program_mode(program: Dict[str, Any]) -> str:
    metadata = dict((program.get("spec") or {}).get("metadata") or program.get("metadata") or {})
    return str(metadata.get("mode") or "production").strip().lower()


def _target_ref(item: Dict[str, Any]) -> Optional[str]:
    return (
        str(item.get("target_comment_url") or "").strip()
        or str(item.get("target_url") or "").strip()
        or str(((item.get("discovered_target") or {}).get("target_comment_url") or "")).strip()
        or str(((item.get("discovered_target") or {}).get("target_url") or "")).strip()
        or None
    )


def _normalized_target_ref(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().rstrip("/")
    return normalized or None


def _thread_ref_from_item(item: Dict[str, Any]) -> Optional[str]:
    value = (
        str(item.get("thread_url") or "").strip()
        or str(((item.get("discovered_target") or {}).get("thread_url") or "")).strip()
        or _thread_root_from_comment_url(item.get("target_comment_url"))
        or str(item.get("target_url") or "").strip()
    )
    normalized = value.rstrip("/")
    return normalized or None


def _failure_classification(result: Dict[str, Any]) -> str:
    failure_class = str(result.get("failure_class") or "").strip().lower()
    if failure_class == "profile_capability":
        return "profile_capability"
    if failure_class == "community_restricted":
        return "community_restricted"
    error_text = str(result.get("error") or "").lower()
    if error_text.find("session not found") >= 0:
        return "session_invalid"
    if "banned from this community" in error_text:
        return "community_restricted"
    if "unable to create comment" in error_text or "something went wrong" in error_text:
        return "submit_rejected"
    verdict = str(result.get("final_verdict") or "")
    if verdict == "infra_failure" or is_infra_error_text(result.get("error")):
        return "infrastructure"
    if "not found" in error_text:
        return "target_unavailable"
    if not result.get("attempt_id"):
        return "verification_miss"
    return "execution_failed"


def _should_regenerate_generated_comment(
    item: Dict[str, Any],
    *,
    classification: str,
    error: str,
) -> bool:
    action = str(item.get("action") or "").strip()
    if action not in {"comment_post", "reply_comment"}:
        return False
    if not str(item.get("text") or "").strip() and not item.get("generation_evidence"):
        return False
    normalized_error = str(error or "").lower()
    if "comment verification failed" in normalized_error:
        return True
    return classification in {"target_unavailable", "submit_rejected", "verification_miss"}


def _normalize_actor_username(value: Optional[str]) -> str:
    return str(value or "").strip().lstrip("u/").lstrip("/").lower()


def _thread_root_from_comment_url(url: Optional[str]) -> Optional[str]:
    clean = str(url or "").split("?", 1)[0].strip().rstrip("/")
    marker = "/comments/"
    if marker not in clean:
        return None
    parts = clean.split(marker, 1)
    suffix = parts[1].split("/")
    if len(suffix) < 2:
        return None
    return f"{parts[0]}{marker}{suffix[0]}/{suffix[1]}/"


class RedditProgramOrchestrator:
    def __init__(
        self,
        *,
        store: RedditProgramStore,
        proxy_resolver: Optional[Callable[[], Optional[str]]] = None,
        broadcast_update: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
        action_runner=run_reddit_action,
        content_generator: Optional[RedditGrowthContentGenerator] = None,
        notification_service: Optional[RedditProgramNotificationService] = None,
    ):
        self.store = store
        self.proxy_resolver = proxy_resolver or (lambda: None)
        self.broadcast_update = broadcast_update
        self.action_runner = action_runner
        self.content_generator = content_generator or RedditGrowthContentGenerator()
        self.notification_service = notification_service or RedditProgramNotificationService()
        self._lock = asyncio.Lock()
        self._program_locks: Dict[str, asyncio.Lock] = {}
        self._subreddit_post_cache: Dict[tuple[str, tuple[str, ...], int], List[Dict[str, Any]]] = {}
        self._thread_payload_cache: Dict[str, Any] = {}
        self._thread_comment_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._cross_program_target_ref_cache: Dict[tuple[str, int], set[str]] = {}

    def _program_lock(self, program_id: str) -> asyncio.Lock:
        existing = self._program_locks.get(program_id)
        if existing is not None:
            return existing
        created = asyncio.Lock()
        self._program_locks[program_id] = created
        return created

    def _clear_run_caches(self) -> None:
        self._subreddit_post_cache.clear()
        self._thread_payload_cache.clear()
        self._thread_comment_cache.clear()
        self._cross_program_target_ref_cache.clear()

    async def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.broadcast_update:
            return
        try:
            await self.broadcast_update(event_type, payload)
        except Exception as exc:
            logger.warning(f"reddit program broadcast failure ({event_type}): {exc}")

    async def process_due_programs(self, *, max_programs: int = 2) -> Dict[str, int]:
        processed = 0
        failed = 0
        async with self._lock:
            for program in self.store.get_due_programs():
                if processed >= max_programs:
                    break
                if self._program_lock(str(program["id"])).locked():
                    logger.info(f"reddit program already processing; skipping due tick for {program['id']}")
                    continue
                try:
                    await self.process_program(program["id"], force_due=False)
                    processed += 1
                except RedditProgramAlreadyRunningError:
                    logger.info(f"reddit program already processing; skipping duplicate due tick for {program['id']}")
                except Exception as exc:
                    failed += 1
                    logger.error(f"reddit program failed {program['id']}: {exc}", exc_info=True)
        return {"processed": processed, "failed": failed}

    async def process_program(self, program_id: str, *, force_due: bool = True) -> Dict[str, Any]:
        program_lock = self._program_lock(program_id)
        if program_lock.locked():
            raise RedditProgramAlreadyRunningError(f"reddit program already running: {program_id}")

        async with program_lock:
            self._clear_run_caches()
            program = self.store.get_program(program_id)
            if not program:
                raise ValueError(f"reddit program not found: {program_id}")

            if program.get("status") != "active":
                return {"program_id": program_id, "processed": 0, "status": program.get("status")}

            await self._emit("reddit_program_start", {"program_id": program_id, "status": program.get("status")})

            now = _utc_now()
            processed = 0
            selected_ids = [str(item.get("id") or "") for item in self._select_due_items(program, now=now, force_due=force_due)]
            for work_item_id in selected_ids:
                latest = self.store.get_program(program_id)
                if not latest:
                    break
                if latest.get("status") != "active":
                    program = latest
                    break
                current_item = next((entry for entry in ((latest.get("compiled") or {}).get("work_items") or []) if entry.get("id") == work_item_id), None)
                if not current_item or str(current_item.get("status") or "pending") != "pending":
                    program = latest
                    continue
                updated = await self._run_work_item(latest, work_item_id)
                program = updated
                processed += 1

            program["last_run_at"] = _utc_iso(now)
            program["last_result"] = {"processed": processed}
            saved = self.store.save_program(program)
            saved = await self._maybe_send_rollup_notifications(saved)
            await self._emit(
                "reddit_program_complete",
                {
                    "program_id": program_id,
                    "processed": processed,
                    "status": saved.get("status"),
                    "remaining_contract": saved.get("remaining_contract", {}),
                },
            )
            return {"program_id": program_id, "processed": processed, "status": saved.get("status")}

    def _select_due_items(self, program: Dict[str, Any], *, now: datetime, force_due: bool) -> List[Dict[str, Any]]:
        work_items = list(((program.get("compiled") or {}).get("work_items") or []))
        policy = _execution_policy(program)
        max_actions = max(1, int(policy.get("max_actions_per_tick", 3)))
        cooldown_minutes = max(0, int(policy.get("cooldown_minutes", 15)))
        selected: List[Dict[str, Any]] = []
        last_profile_at: Dict[str, datetime] = {}

        for item in work_items:
            if len(selected) >= max_actions:
                break
            if str(item.get("status") or "pending") != "pending":
                continue
            scheduled_at = _parse_iso(item.get("scheduled_at"))
            if not force_due and scheduled_at and scheduled_at > now:
                continue
            profile_name = str(item.get("profile_name") or "")
            if self._profile_requires_mandatory_join(program, profile_name=profile_name) and str(item.get("action") or "") != "join_subreddit":
                continue
            profile_last = last_profile_at.get(profile_name) or self._latest_profile_attempt(program, profile_name)
            if cooldown_minutes > 0 and profile_last and profile_last > now - timedelta(minutes=cooldown_minutes):
                continue
            selected.append(item)
            last_profile_at[profile_name] = now

        return selected

    def _latest_profile_attempt(self, program: Dict[str, Any], profile_name: str) -> Optional[datetime]:
        latest: Optional[datetime] = None
        for item in (program.get("compiled") or {}).get("work_items", []):
            if str(item.get("profile_name") or "") != profile_name:
                continue
            last_attempt = _parse_iso(item.get("last_attempt_at"))
            if last_attempt and (latest is None or last_attempt > latest):
                latest = last_attempt
        return latest

    def _profile_requires_mandatory_join(self, program: Dict[str, Any], *, profile_name: str) -> bool:
        mandatory = list((_program_filters(program).get("mandatory_join_urls") or []))
        if not mandatory:
            return False
        for item in (program.get("compiled") or {}).get("work_items", []):
            if str(item.get("profile_name") or "") != profile_name:
                continue
            if str(item.get("action") or "") != "join_subreddit":
                continue
            if str(item.get("status") or "pending") != "completed":
                return True
        return False

    async def _run_work_item(self, program: Dict[str, Any], work_item_id: str) -> Dict[str, Any]:
        work_items = (program.get("compiled") or {}).get("work_items", [])
        item = next((entry for entry in work_items if entry.get("id") == work_item_id), None)
        if not item:
            return program

        item["status"] = "running"
        item["attempts"] = int(item.get("attempts", 0)) + 1
        item["last_attempt_at"] = _utc_iso()
        program.setdefault("events", []).append(
            {
                "timestamp": _utc_iso(),
                "type": "work_item_start",
                "work_item_id": work_item_id,
                "action": item.get("action"),
                "profile_name": item.get("profile_name"),
            }
        )
        program["events"] = list(program.get("events", []))[-200:]
        program = self.store.save_program(program)
        work_items = (program.get("compiled") or {}).get("work_items", [])
        item = next((entry for entry in work_items if entry.get("id") == work_item_id), None)
        if not item:
            return program

        session = RedditSession(str(item.get("profile_name") or ""))
        if not session.load():
            return self._finalize_without_execution(
                program,
                item,
                status="blocked",
                error=f"reddit session not found: {item.get('profile_name')}",
                failure_class="session_invalid",
            )

        actor_username = session.get_username()
        capability_error = await self._profile_capability_error(
            program,
            item=item,
            session=session,
            actor_username=actor_username,
        )
        if capability_error:
            self._record_failure(
                program,
                item,
                {
                    "success": False,
                    "error": capability_error,
                    "failure_class": "profile_capability",
                    "final_verdict": "failed_confirmed",
                    "subreddit": item.get("subreddit"),
                },
                None,
                consume_attempt=False,
            )
            return self.store.save_program(program)

        resolution_timeout_seconds = max(1, int(_execution_policy(program).get("target_resolution_timeout_seconds", 90)))
        try:
            target_payload = await asyncio.wait_for(
                self._resolve_target(program, item, actor_username=actor_username),
                timeout=resolution_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._finalize_resolution_failure(
                program,
                item,
                error=f"reddit target resolution timed out after {resolution_timeout_seconds}s",
                failure_class="target_resolution_timeout",
                retryable=True,
            )
        if target_payload.get("error"):
            return self._finalize_resolution_failure(
                program,
                item,
                error=str(target_payload["error"]),
                failure_class=str(target_payload.get("failure_class") or "target_unavailable"),
                retryable=bool(target_payload.get("retryable", False)),
                consume_attempt=bool(target_payload.get("consume_attempt", False)),
            )

        action = str(item.get("action") or "")
        proxy_url = self.proxy_resolver() if callable(self.proxy_resolver) else None
        generation_evidence = dict(target_payload.get("generation_evidence") or {})
        item["discovered_target"] = target_payload.get("discovered_target")
        if target_payload.get("target_url"):
            item["target_url"] = target_payload.get("target_url")
        if target_payload.get("target_comment_url"):
            item["target_comment_url"] = target_payload.get("target_comment_url")
        if generation_evidence:
            item["generation_evidence"] = generation_evidence
            item["generation_feedback"] = None
            self._remember_generated_text(program, item, generation_evidence)
        result = await self.action_runner(
            session,
            action=action,
            proxy_url=proxy_url,
            url=target_payload.get("target_url"),
            target_comment_url=target_payload.get("target_comment_url"),
            text=target_payload.get("text"),
            title=target_payload.get("title"),
            body=target_payload.get("body"),
            subreddit=target_payload.get("subreddit"),
            user_flair_hint=target_payload.get("user_flair_hint"),
            auto_user_flair=bool(target_payload.get("auto_user_flair", False)),
            forensic_context={
                "run_id": program["id"],
                "campaign_id": program["id"],
                "job_id": work_item_id,
                "engine": f"reddit_program_{action}",
                "metadata": {
                    "program_id": program["id"],
                    "work_item_id": work_item_id,
                    "local_date": item.get("local_date"),
                    "source": item.get("source"),
                    "generation_evidence": generation_evidence or None,
                },
            },
        )

        if (
            action == "create_post"
            and bool(result.get("success"))
            and not str(item.get("target_url") or "").strip()
            and str(result.get("current_url") or "").strip()
        ):
            item["target_url"] = str(result.get("target_url") or result.get("current_url") or "").strip()

        success, verification_error = self._result_satisfies_contract(program, item, result)
        if success:
            item["status"] = "completed"
            item["completed_at"] = _utc_iso()
            item["error"] = None
            item["result"] = self._compact_result(result)
            self._append_target_history(program, item, result, actor_username=actor_username)
        else:
            self._record_failure(
                program,
                item,
                result,
                verification_error,
                actor_username=actor_username,
            )

        program.setdefault("events", []).append(
            {
                "timestamp": _utc_iso(),
                "type": "work_item_complete",
                "work_item_id": work_item_id,
                "action": item.get("action"),
                "profile_name": item.get("profile_name"),
                "success": item.get("status") == "completed",
                "status": item.get("status"),
                "error": item.get("error"),
                "attempt_id": ((item.get("result") or {}).get("attempt_id") if isinstance(item.get("result"), dict) else None),
            }
        )
        program["events"] = list(program.get("events", []))[-200:]
        saved = self.store.save_program(program)
        saved = await self._maybe_send_item_notification(saved, work_item_id=work_item_id)
        return saved

    def _finalize_resolution_failure(
        self,
        program: Dict[str, Any],
        item: Dict[str, Any],
        *,
        error: str,
        failure_class: str,
        retryable: bool,
        consume_attempt: bool = True,
    ) -> Dict[str, Any]:
        policy = _execution_policy(program)
        max_attempts = max(1, int(policy.get("max_attempts_per_item", 5)))
        retry_delay_minutes = max(1, int(policy.get("retry_delay_minutes", 20)))

        if not consume_attempt:
            item["attempts"] = max(0, int(item.get("attempts", 0)) - 1)
        item["error"] = error
        item["result"] = {
            "success": False,
            "error": error,
            "failure_class": failure_class,
            "final_verdict": "needs_review",
        }
        if retryable and int(item.get("attempts", 0)) < max_attempts:
            item["status"] = "pending"
            item["scheduled_at"] = _utc_iso(_utc_now() + timedelta(minutes=retry_delay_minutes))
        else:
            item["status"] = "blocked"
        return self.store.save_program(program)

    def _finalize_without_execution(
        self,
        program: Dict[str, Any],
        item: Dict[str, Any],
        *,
        status: str,
        error: str,
        failure_class: str,
    ) -> Dict[str, Any]:
        item["status"] = status
        item["error"] = error
        item["result"] = {
            "success": False,
            "error": error,
            "failure_class": failure_class,
            "final_verdict": "failed_confirmed",
        }
        return self.store.save_program(program)

    def _remember_generated_text(self, program: Dict[str, Any], item: Dict[str, Any], generation_evidence: Dict[str, Any]) -> None:
        candidates = list(program.get("generated_text_history") or [])
        for field in ("text", "title", "body", "combined_text"):
            value = str(generation_evidence.get(field) or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        program["generated_text_history"] = candidates[-500:]

        combined_text = str(generation_evidence.get("combined_text") or generation_evidence.get("text") or "").strip()
        if combined_text:
            records = list(program.get("generated_text_records") or [])
            records.append(
                {
                    "timestamp": _utc_iso(),
                    "profile_name": item.get("profile_name"),
                    "local_date": item.get("local_date"),
                    "action": item.get("action"),
                    "thread_url": generation_evidence.get("thread_url") or _thread_ref_from_item(item),
                    "target_url": item.get("target_url"),
                    "target_comment_url": item.get("target_comment_url"),
                    "combined_text": combined_text,
                }
            )
            program["generated_text_records"] = records[-500:]

    def _generated_text_scope(
        self,
        program: Dict[str, Any],
        *,
        profile_name: Optional[str] = None,
        local_date: Optional[str] = None,
        thread_url: Optional[str] = None,
    ) -> List[str]:
        normalized_thread = _normalized_target_ref(thread_url)
        results: List[str] = []
        for record in list(program.get("generated_text_records") or []):
            if profile_name and str(record.get("profile_name") or "") != profile_name:
                continue
            if local_date and str(record.get("local_date") or "") != local_date:
                continue
            if normalized_thread and _normalized_target_ref(record.get("thread_url")) != normalized_thread:
                continue
            combined_text = str(record.get("combined_text") or "").strip()
            if combined_text:
                results.append(combined_text)
        return results

    def _reserved_target_refs(self, program: Dict[str, Any], *, exclude_work_item_id: Optional[str] = None) -> set[str]:
        reserved: set[str] = set()
        for entry in list(program.get("target_history") or []):
            normalized = _normalized_target_ref(entry.get("target_ref"))
            if normalized:
                reserved.add(normalized)
        for work_item in ((program.get("compiled") or {}).get("work_items") or []):
            if exclude_work_item_id and str(work_item.get("id") or "") == exclude_work_item_id:
                continue
            if str(work_item.get("status") or "") == "cancelled":
                continue
            normalized = _normalized_target_ref(_target_ref(work_item))
            if normalized:
                reserved.add(normalized)
        return reserved

    def _cross_program_reserved_target_refs(self, program: Dict[str, Any]) -> set[str]:
        lookback_days = max(0, int(_execution_policy(program).get("cross_program_target_lookback_days", 30) or 0))
        if lookback_days <= 0:
            return set()
        program_id = str(program.get("id") or "")
        cache_key = (program_id, lookback_days)
        cached = self._cross_program_target_ref_cache.get(cache_key)
        if cached is not None:
            return set(cached)

        cutoff = _utc_now() - timedelta(days=lookback_days)
        reserved: set[str] = set()
        for other_program in self.store.list_programs():
            other_program_id = str(other_program.get("id") or "")
            if not other_program_id or other_program_id == program_id:
                continue
            other_created_at = _parse_iso(other_program.get("created_at"))
            for entry in list(other_program.get("target_history") or []):
                timestamp = _parse_iso(entry.get("timestamp")) or other_created_at
                if timestamp and timestamp < cutoff:
                    continue
                normalized = _normalized_target_ref(entry.get("target_ref"))
                if normalized:
                    reserved.add(normalized)
            if str(other_program.get("status") or "") != "active":
                continue
            for work_item in ((other_program.get("compiled") or {}).get("work_items") or []):
                if str(work_item.get("status") or "") == "cancelled":
                    continue
                normalized = _normalized_target_ref(_target_ref(work_item))
                if not normalized:
                    continue
                freshness = (
                    _parse_iso(work_item.get("last_attempt_at"))
                    or _parse_iso(work_item.get("scheduled_at"))
                    or other_created_at
                )
                if freshness and freshness < cutoff:
                    continue
                reserved.add(normalized)
        self._cross_program_target_ref_cache[cache_key] = set(reserved)
        return reserved

    def _thread_usage_stats(
        self,
        program: Dict[str, Any],
        *,
        local_date: str,
        thread_url: Optional[str],
        exclude_work_item_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_thread = _normalized_target_ref(thread_url)
        if not normalized_thread:
            return {"count": 0, "profiles": set(), "authors": collections.Counter(), "subreddits": collections.Counter()}
        count = 0
        profiles: set[str] = set()
        authors: collections.Counter[str] = collections.Counter()
        subreddits: collections.Counter[str] = collections.Counter()
        for entry in list(program.get("target_history") or []):
            if str(entry.get("local_date") or "") != local_date:
                continue
            if _normalized_target_ref(entry.get("thread_url") or entry.get("target_url")) != normalized_thread:
                continue
            count += 1
            profiles.add(str(entry.get("profile_name") or ""))
            author = str(entry.get("target_author") or "").strip().lower()
            if author:
                authors[author] += 1
            subreddit = str(entry.get("subreddit") or "").strip().lower()
            if subreddit:
                subreddits[subreddit] += 1
        for work_item in ((program.get("compiled") or {}).get("work_items") or []):
            if exclude_work_item_id and str(work_item.get("id") or "") == exclude_work_item_id:
                continue
            if str(work_item.get("local_date") or "") != local_date:
                continue
            if str(work_item.get("status") or "") == "cancelled":
                continue
            if _normalized_target_ref(_thread_ref_from_item(work_item)) != normalized_thread:
                continue
            count += 1
            profiles.add(str(work_item.get("profile_name") or ""))
            candidate = dict(work_item.get("discovered_target") or {})
            author = str(candidate.get("author") or "").strip().lower()
            if author:
                authors[author] += 1
            subreddit = str(work_item.get("subreddit") or candidate.get("subreddit") or "").strip().lower()
            if subreddit:
                subreddits[subreddit] += 1
        return {"count": count, "profiles": profiles, "authors": authors, "subreddits": subreddits}

    def _own_content_allowed(self, program: Dict[str, Any]) -> bool:
        if _program_mode(program) in {"qa", "test"}:
            return True
        return not bool(_realism_policy(program).get("forbid_own_content_interactions", True))

    def _thread_already_used_by_profile(
        self,
        program: Dict[str, Any],
        *,
        profile_name: str,
        local_date: str,
        thread_url: Optional[str],
        actions: Optional[set[str]] = None,
    ) -> bool:
        normalized_thread = _normalized_target_ref(thread_url)
        if not normalized_thread:
            return False
        allowed_actions = actions or {"create_post", "comment_post", "reply_comment"}
        for entry in list(program.get("target_history") or []):
            if str(entry.get("profile_name") or "") != profile_name:
                continue
            if str(entry.get("local_date") or "") != local_date:
                continue
            if str(entry.get("action") or "") not in allowed_actions:
                continue
            entry_thread = _normalized_target_ref(entry.get("thread_url") or entry.get("target_url"))
            if entry_thread == normalized_thread:
                return True
        for work_item in ((program.get("compiled") or {}).get("work_items") or []):
            if str(work_item.get("profile_name") or "") != profile_name:
                continue
            if str(work_item.get("local_date") or "") != local_date:
                continue
            if str(work_item.get("action") or "") not in allowed_actions:
                continue
            if _normalized_target_ref(_thread_ref_from_item(work_item)) == normalized_thread:
                return True
        return False

    def _candidate_subreddits_for_item(
        self,
        program: Dict[str, Any],
        *,
        item: Dict[str, Any],
        profile_name: str,
        action: str,
    ) -> List[str]:
        available = self._available_subreddits_for_profile(program, profile_name=profile_name, action=action)
        explicit_subreddit = normalize_subreddit_name(item.get("subreddit"))
        if explicit_subreddit:
            return [explicit_subreddit] if explicit_subreddit in available else []
        return available

    def _candidate_violates_realism(
        self,
        program: Dict[str, Any],
        *,
        item: Dict[str, Any],
        candidate: Dict[str, Any],
        actor_username: Optional[str],
    ) -> Optional[str]:
        actor = _normalize_actor_username(actor_username)
        if not self._own_content_allowed(program) and actor:
            candidate_author = _normalize_actor_username(candidate.get("author"))
            post_author = _normalize_actor_username(candidate.get("post_author"))
            if actor and actor in {candidate_author, post_author}:
                return "own content interaction is forbidden in production"

        action = str(item.get("action") or "")
        if action == "reply_comment":
            thread_url = str(candidate.get("thread_url") or _thread_root_from_comment_url(candidate.get("target_comment_url")) or "").strip()
            usage = self._thread_usage_stats(
                program,
                local_date=str(item.get("local_date") or ""),
                thread_url=thread_url,
                exclude_work_item_id=str(item.get("id") or ""),
            )
            if usage["count"] > 0:
                return "same-thread multi-profile reply clustering is not allowed"
            if self._thread_already_used_by_profile(
                program,
                profile_name=str(item.get("profile_name") or ""),
                local_date=str(item.get("local_date") or ""),
                thread_url=thread_url,
            ):
                return "same-profile thread loop is not allowed for reply targets"
        return None

    def _compact_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": bool(result.get("success")),
            "error": result.get("error"),
            "failure_class": result.get("failure_class"),
            "attempt_id": result.get("attempt_id"),
            "trace_id": result.get("trace_id"),
            "final_verdict": result.get("final_verdict"),
            "evidence_summary": result.get("evidence_summary"),
            "current_url": result.get("current_url"),
            "target_url": result.get("target_url"),
            "target_comment_url": result.get("target_comment_url"),
            "subreddit": result.get("subreddit"),
            "verification": result.get("verification"),
            "action": result.get("action"),
            "identity_evidence": result.get("identity_evidence"),
        }

    def _result_satisfies_contract(
        self,
        program: Dict[str, Any],
        item: Dict[str, Any],
        result: Dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        contract = _verification_contract(program)
        require_success_confirmed = bool(contract.get("require_success_confirmed", True))
        require_attempt_id = bool(contract.get("require_attempt_id", True))
        require_evidence_summary = bool(contract.get("required_evidence_summary", True))
        require_target_reference = bool(contract.get("required_target_reference", True))

        if not bool(result.get("success")):
            return False, str(result.get("error") or "reddit action reported failure")
        if require_success_confirmed and str(result.get("final_verdict") or "") != "success_confirmed":
            return False, "reddit action was not verified as success_confirmed"
        if require_attempt_id and not str(result.get("attempt_id") or "").strip():
            return False, "reddit action did not produce attempt_id"
        if require_evidence_summary and not str(result.get("evidence_summary") or "").strip():
            return False, "reddit action did not produce evidence_summary"
        if require_target_reference and not _target_ref(item):
            return False, "reddit action has no persisted target reference"
        return True, None

    def _record_failure(
        self,
        program: Dict[str, Any],
        item: Dict[str, Any],
        result: Dict[str, Any],
        verification_error: Optional[str],
        *,
        consume_attempt: bool = True,
        actor_username: Optional[str] = None,
    ) -> None:
        policy = _execution_policy(program)
        max_attempts = max(1, int(policy.get("max_attempts_per_item", 5)))
        retry_delay_minutes = max(1, int(policy.get("retry_delay_minutes", 20)))
        classification = _failure_classification(result)
        error = verification_error or str(result.get("error") or "reddit action failed")

        if not consume_attempt:
            item["attempts"] = max(0, int(item.get("attempts", 0)) - 1)
        item["result"] = self._compact_result(result)
        item["error"] = error
        self._append_target_history(program, item, result, actor_username=actor_username)

        if classification == "community_restricted" and self._retry_discovered_target_after_community_restriction(
            program,
            item=item,
            result=result,
        ):
            return

        if classification in {"community_restricted", "profile_capability"} and self._reroute_after_community_block(program, item=item, result=result):
            return
        if classification in {"session_invalid", "quota_exhausted", "community_restricted", "profile_capability"}:
            item["status"] = "blocked"
            return

        if int(item.get("attempts", 0)) >= max_attempts:
            item["status"] = "exhausted"
            return

        item["status"] = "pending"
        item["scheduled_at"] = _utc_iso(_utc_now() + timedelta(minutes=retry_delay_minutes))
        if classification in {"target_unavailable", "infrastructure", "submit_rejected"}:
            item["discovered_target"] = None
            # Keep explicit targets such as mandatory joins intact across retries.
            if item.get("source", "").startswith("quota_") or item.get("source") == "proof_matrix":
                item["target_url"] = None
                item["target_comment_url"] = None
        if _should_regenerate_generated_comment(item, classification=classification, error=error):
            item["text"] = None
            item["generation_evidence"] = None
            item["generation_feedback"] = {
                "mode": "submit_rejected" if classification == "submit_rejected" else "retry_generation",
                "last_error": error,
                "failed_target_url": result.get("target_url") or item.get("target_url"),
                "failed_target_comment_url": result.get("target_comment_url") or item.get("target_comment_url"),
            }
        if item.get("source") in {"quota_generated_post", "quota_random_reply"}:
            item["text"] = None
            item["title"] = None
            item["body"] = None
            item["generation_evidence"] = None

    def _reroute_after_community_block(
        self,
        program: Dict[str, Any],
        *,
        item: Dict[str, Any],
        result: Dict[str, Any],
    ) -> bool:
        source = str(item.get("source") or "")
        if source not in {"quota_generated_post", "quota_random_reply", "quota_post_upvote", "quota_comment_upvote"}:
            return False

        profile_name = str(item.get("profile_name") or "")
        subreddit = (
            str(item.get("subreddit") or "").strip()
            or str(((item.get("discovered_target") or {}).get("subreddit") or "")).strip()
            or _subreddit_from_url(item.get("target_url"))
            or _subreddit_from_url(item.get("target_comment_url"))
            or _subreddit_from_url(result.get("target_url"))
            or _subreddit_from_url(result.get("target_comment_url"))
            or _subreddit_from_url(result.get("current_url"))
        )
        self._remember_community_block(program, profile_name=profile_name, subreddit=subreddit)
        available = self._available_subreddits_for_profile(
            program,
            profile_name=profile_name,
            action=str(item.get("action") or ""),
        )
        if not available:
            return False

        retry_delay_minutes = max(1, int(_execution_policy(program).get("retry_delay_minutes", 20)))
        item["status"] = "pending"
        item["scheduled_at"] = _utc_iso(_utc_now() + timedelta(minutes=retry_delay_minutes))
        item["discovered_target"] = None
        item["target_url"] = None
        item["target_comment_url"] = None

        if source == "quota_generated_post":
            current_subreddit = str(item.get("subreddit") or "").strip().lower()
            next_subreddit = next(
                (candidate for candidate in available if candidate.lower() != current_subreddit),
                available[0],
            )
            item["subreddit"] = next_subreddit
            item["title"] = None
            item["body"] = None
            item["generation_evidence"] = None
        elif source == "quota_random_reply":
            item["text"] = None
            item["generation_evidence"] = None

        item["error"] = f"rerouting after community restriction in r/{subreddit}" if subreddit else "rerouting after community restriction"
        return True

    def _retry_discovered_target_after_community_restriction(
        self,
        program: Dict[str, Any],
        *,
        item: Dict[str, Any],
        result: Dict[str, Any],
    ) -> bool:
        target_mode = str(item.get("target_mode") or "").strip().lower()
        if target_mode not in {"discover_post", "discover_comment"}:
            return False

        retry_delay_minutes = max(1, int(_execution_policy(program).get("retry_delay_minutes", 20)))
        item["status"] = "pending"
        item["scheduled_at"] = _utc_iso(_utc_now() + timedelta(minutes=retry_delay_minutes))
        item["discovered_target"] = None
        item["target_url"] = None
        item["target_comment_url"] = None
        if item.get("generation_evidence"):
            item["text"] = None
            item["title"] = None
            item["body"] = None
            item["generation_evidence"] = None
        subreddit = (
            str(item.get("subreddit") or "").strip()
            or str(result.get("subreddit") or "").strip()
            or _subreddit_from_url(result.get("target_url"))
            or _subreddit_from_url(result.get("target_comment_url"))
        )
        item["error"] = (
            f"retrying new target after community restriction in r/{subreddit}"
            if subreddit
            else "retrying new target after community restriction"
        )
        return True

    def _append_target_history(self, program: Dict[str, Any], item: Dict[str, Any], result: Dict[str, Any], *, actor_username: Optional[str] = None) -> None:
        target_ref = _target_ref(item) or str(result.get("target_comment_url") or result.get("target_url") or "").strip()
        if not target_ref:
            return
        entry = {
            "timestamp": _utc_iso(),
            "profile_name": item.get("profile_name"),
            "actor_username": actor_username,
            "local_date": item.get("local_date"),
            "action": item.get("action"),
            "target_ref": target_ref,
            "thread_url": item.get("thread_url") or ((item.get("discovered_target") or {}).get("thread_url")) or _thread_root_from_comment_url(item.get("target_comment_url")),
            "target_url": item.get("target_url"),
            "target_comment_url": item.get("target_comment_url"),
            "subreddit": item.get("subreddit") or ((item.get("discovered_target") or {}).get("subreddit")) or result.get("subreddit"),
            "target_author": ((item.get("discovered_target") or {}).get("author")),
            "attempt_id": result.get("attempt_id"),
            "success": bool(result.get("success")),
            "final_verdict": result.get("final_verdict"),
            "failure_class": result.get("failure_class"),
        }
        history = list(program.get("target_history") or [])
        history.append(entry)
        program["target_history"] = history[-500:]
        self._cross_program_target_ref_cache.clear()

    async def _maybe_send_item_notification(self, program: Dict[str, Any], *, work_item_id: str) -> Dict[str, Any]:
        item = next((entry for entry in ((program.get("compiled") or {}).get("work_items") or []) if entry.get("id") == work_item_id), None)
        if not item:
            return program
        if str(item.get("status") or "") not in {"blocked", "exhausted"}:
            return program
        await self.notification_service.send_program_email(
            program,
            key=f"hard_failure_summary_only:{work_item_id}:{item.get('attempts')}",
            kind="hard_failure",
            subject=f"reddit program hard failure: {program.get('id')} / {item.get('action')}",
            body=build_program_email_body(
                program,
                headline=f"hard failure for {item.get('action')} on {item.get('profile_name')}: {item.get('error')}",
            ),
            metadata={
                "work_item_id": work_item_id,
                "action": item.get("action"),
                "profile_name": item.get("profile_name"),
                "status": item.get("status"),
                "summary_only": True,
            },
        )
        return self.store.save_program(program)

    async def _maybe_send_rollup_notifications(self, program: Dict[str, Any]) -> Dict[str, Any]:
        program = await self._maybe_send_daily_summaries(program)
        program = await self._maybe_send_terminal_notification(program)
        return program

    async def _maybe_send_daily_summaries(self, program: Dict[str, Any]) -> Dict[str, Any]:
        daily_progress = dict(program.get("daily_progress") or {})
        for local_date in sorted(daily_progress.keys()):
            if any(
                str(item.get("local_date") or "") == local_date and str(item.get("status") or "") in {"pending", "running"}
                for item in ((program.get("compiled") or {}).get("work_items") or [])
            ):
                continue
            subject = f"reddit program day summary: {program.get('id')} / {local_date}"
            body = build_program_email_body(program, headline=f"daily summary for {local_date}")
            await self.notification_service.send_program_email(
                program,
                key=f"daily_summary:{local_date}",
                kind="daily_summary",
                subject=subject,
                body=body,
                metadata={"local_date": local_date},
            )
        return self.store.save_program(program)

    async def _maybe_send_terminal_notification(self, program: Dict[str, Any]) -> Dict[str, Any]:
        if str(program.get("status") or "") not in {"completed", "exhausted", "cancelled"}:
            return program
        subject = f"reddit program {program.get('status')}: {program.get('id')}"
        body = build_program_email_body(program, headline=f"program {program.get('status')}")
        await self.notification_service.send_program_email(
            program,
            key=f"terminal:{program.get('status')}",
            kind="terminal",
            subject=subject,
            body=body,
            metadata={"status": program.get("status")},
        )
        return self.store.save_program(program)

    async def _resolve_target(self, program: Dict[str, Any], item: Dict[str, Any], *, actor_username: Optional[str] = None) -> Dict[str, Any]:
        if item.get("target_mode") == "explicit":
            subreddit = item.get("subreddit") or normalize_subreddit_name(item.get("target_url"))
            policy_metadata = self._subreddit_policy_metadata(
                program,
                subreddit=subreddit,
                profile_name=str(item.get("profile_name") or ""),
                action=str(item.get("action") or ""),
            )
            return {
                "target_url": item.get("target_url"),
                "target_comment_url": item.get("target_comment_url"),
                "text": item.get("text"),
                "title": item.get("title"),
                "body": item.get("body"),
                "subreddit": subreddit,
                "user_flair_hint": policy_metadata.get("configured_user_flair"),
                "auto_user_flair": policy_metadata.get("auto_user_flair"),
                "discovered_target": None,
                "generation_evidence": item.get("generation_evidence"),
            }

        if item.get("target_mode") == "generate_post":
            return await self._build_generated_post_payload(program, item)

        if item.get("target_mode") == "discover_post":
            candidate = await self._discover_post_target(program, item, actor_username=actor_username)
            if not candidate:
                return {
                    "error": "no eligible reddit post targets available for this quota item",
                    "failure_class": "target_unavailable",
                    "retryable": True,
                }
            text = item.get("text")
            generation_evidence = item.get("generation_evidence")
            if str(item.get("action") or "") == "comment_post" and not str(text or "").strip():
                generated = await self._build_generated_comment_payload(program, item, candidate)
                if generated.get("error"):
                    return generated
                text = generated.get("text")
                generation_evidence = generated.get("generation_evidence")
            return {
                "target_url": candidate.get("target_url"),
                "target_comment_url": None,
                "text": text,
                "title": item.get("title"),
                "body": item.get("body"),
                "subreddit": candidate.get("subreddit") or item.get("subreddit"),
                "user_flair_hint": self._subreddit_policy_metadata(
                    program,
                    subreddit=candidate.get("subreddit") or item.get("subreddit"),
                    profile_name=str(item.get("profile_name") or ""),
                    action=str(item.get("action") or ""),
                ).get("configured_user_flair"),
                "auto_user_flair": self._subreddit_policy_metadata(
                    program,
                    subreddit=candidate.get("subreddit") or item.get("subreddit"),
                    profile_name=str(item.get("profile_name") or ""),
                    action=str(item.get("action") or ""),
                ).get("auto_user_flair"),
                "discovered_target": candidate,
                "generation_evidence": generation_evidence,
            }

        if item.get("target_mode") == "discover_comment":
            candidate = await self._discover_comment_target(program, item, actor_username=actor_username)
            if not candidate:
                return {
                    "error": "no eligible reddit comment targets available for this quota item",
                    "failure_class": "target_unavailable",
                    "retryable": True,
                }
            reply_text = item.get("text")
            generation_evidence = item.get("generation_evidence")
            if str(item.get("action") or "") == "reply_comment" and not str(reply_text or "").strip():
                generated = await self._build_generated_reply_payload(program, item, candidate)
                if generated.get("error"):
                    return generated
                reply_text = generated.get("text")
                generation_evidence = generated.get("generation_evidence")
            return {
                "target_url": candidate.get("thread_url"),
                "target_comment_url": candidate.get("target_comment_url"),
                "text": reply_text,
                "title": item.get("title"),
                "body": item.get("body"),
                "subreddit": item.get("subreddit"),
                "user_flair_hint": self._subreddit_policy_metadata(
                    program,
                    subreddit=item.get("subreddit") or candidate.get("subreddit"),
                    profile_name=str(item.get("profile_name") or ""),
                    action=str(item.get("action") or ""),
                ).get("configured_user_flair"),
                "auto_user_flair": self._subreddit_policy_metadata(
                    program,
                    subreddit=item.get("subreddit") or candidate.get("subreddit"),
                    profile_name=str(item.get("profile_name") or ""),
                    action=str(item.get("action") or ""),
                ).get("auto_user_flair"),
                "discovered_target": candidate,
                "generation_evidence": generation_evidence,
            }

        return {
            "error": f"unsupported reddit target mode: {item.get('target_mode')}",
            "failure_class": "configuration_error",
            "retryable": False,
        }

    async def _build_generated_post_payload(self, program: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        profile_name = str(item.get("profile_name") or "")
        subreddit = str(item.get("subreddit") or "").strip()
        subreddits = self._available_subreddits_for_profile(program, profile_name=profile_name, action="create_post")
        if subreddit and subreddit.lower() not in {value.lower() for value in subreddits}:
            subreddit = ""
        if not subreddit:
            subreddit = subreddits[0] if subreddits else ""
        if not subreddit:
            return {
                "error": "no subreddit is available for generated reddit post",
                "failure_class": "configuration_error",
                "retryable": False,
            }
        policy_metadata = self._subreddit_policy_metadata(
            program,
            subreddit=subreddit,
            profile_name=profile_name,
            action="create_post",
        )
        keywords = self._keywords_for_subreddit(program, subreddit=subreddit)
        style_samples = await self._style_samples_for_subreddit(program, subreddit=subreddit, keywords=keywords)
        conversation_context = await self._conversation_context_for_subreddit(program, subreddit=subreddit, keywords=keywords)
        recent_texts = list(program.get("generated_text_history") or [])
        same_profile_texts = self._generated_text_scope(program, profile_name=profile_name)
        generated = await self.content_generator.generate_post(
            profile_name=profile_name,
            subreddit=subreddit,
            keywords=keywords,
            style_samples=style_samples,
            conversation_context=conversation_context,
            recent_texts=recent_texts,
            same_profile_texts=same_profile_texts,
        )
        if not generated.success:
            return {
                "error": str(generated.error or "generated reddit post failed"),
                "failure_class": "generation_failed",
                "retryable": True,
                "consume_attempt": False,
            }
        evidence = {
            "kind": "create_post",
            "subreddit": subreddit,
            "title": generated.title,
            "body": generated.body,
            "combined_text": "\n".join(part for part in [generated.title, generated.body] if part).strip(),
            "style_summary": generated.style_summary,
            "conversation_summary": generated.conversation_summary,
            "conversation_samples": conversation_context,
            "sample_urls": generated.sample_urls,
            "validation": generated.validation,
            "policy_validation": generated.validation,
            "novelty_validation": {
                "nearby_duplicate": bool((generated.validation or {}).get("nearby_duplicate")),
                "context_overlap_terms": list((generated.validation or {}).get("context_overlap_terms") or []),
                "similarity_checks": dict((generated.validation or {}).get("similarity_checks") or {}),
            },
            "writing_rules": get_writing_rule_snapshot(),
            "rule_source_hashes": dict(((generated.writing_rule_snapshot or {}).get("rule_source_hashes") or {})),
            "rule_source_paths": list(((generated.writing_rule_snapshot or {}).get("source_paths") or [])),
            "persona_id": (generated.persona_snapshot or {}).get("persona_id"),
            "persona_role": (generated.persona_snapshot or {}).get("default_role"),
            "case_style_applied": (generated.persona_snapshot or {}).get("case_style"),
            "persona": generated.persona_snapshot,
            "word_count": generated.word_count,
            "subreddit_policy": policy_metadata,
            "raw_response": generated.raw_response,
        }
        return {
            "target_url": None,
            "target_comment_url": None,
            "text": None,
            "title": generated.title,
            "body": generated.body,
            "subreddit": subreddit,
            "user_flair_hint": policy_metadata.get("configured_user_flair"),
            "auto_user_flair": policy_metadata.get("auto_user_flair"),
            "discovered_target": None,
            "generation_evidence": evidence,
        }

    async def _build_generated_comment_payload(self, program: Dict[str, Any], item: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        profile_name = str(item.get("profile_name") or "")
        subreddit = str(candidate.get("subreddit") or item.get("subreddit") or "").strip()
        thread_url = str(candidate.get("target_url") or candidate.get("thread_url") or "").strip()
        retry_feedback = dict(item.get("generation_feedback") or {})
        policy_metadata = self._subreddit_policy_metadata(
            program,
            subreddit=subreddit,
            profile_name=profile_name,
            action="comment_post",
        )
        keywords = self._keywords_for_subreddit(program, subreddit=subreddit)
        style_samples = await self._style_samples_for_subreddit(program, subreddit=subreddit, keywords=keywords)
        conversation_context = await self._conversation_context_for_subreddit(program, subreddit=subreddit, keywords=keywords)
        recent_texts = list(program.get("generated_text_history") or [])
        same_thread_texts = self._generated_text_scope(program, thread_url=thread_url)
        same_profile_texts = self._generated_text_scope(program, profile_name=profile_name)
        generated = await self.content_generator.generate_comment(
            profile_name=profile_name,
            subreddit=subreddit,
            thread_title=str(candidate.get("title") or candidate.get("post_title") or ""),
            thread_excerpt=str(candidate.get("body_excerpt") or candidate.get("post_body_excerpt") or ""),
            thread_author=candidate.get("author") or candidate.get("post_author"),
            keywords=keywords,
            style_samples=style_samples,
            conversation_context=conversation_context,
            recent_texts=recent_texts,
            same_thread_texts=same_thread_texts,
            same_profile_texts=same_profile_texts,
            retry_feedback=retry_feedback,
        )
        if not generated.success:
            return {
                "error": str(generated.error or "generated reddit comment failed"),
                "failure_class": "generation_failed",
                "retryable": True,
                "consume_attempt": False,
            }
        evidence = {
            "kind": "comment_post",
            "subreddit": subreddit,
            "text": generated.text,
            "combined_text": str(generated.text or "").strip(),
            "thread_url": thread_url,
            "thread_title": str(candidate.get("title") or candidate.get("post_title") or ""),
            "thread_excerpt": str(candidate.get("body_excerpt") or candidate.get("post_body_excerpt") or ""),
            "target_author": candidate.get("author") or candidate.get("post_author"),
            "style_summary": generated.style_summary,
            "conversation_summary": generated.conversation_summary,
            "conversation_samples": conversation_context,
            "sample_urls": generated.sample_urls,
            "validation": generated.validation,
            "policy_validation": generated.validation,
            "novelty_validation": {
                "nearby_duplicate": bool((generated.validation or {}).get("nearby_duplicate")),
                "context_overlap_terms": list((generated.validation or {}).get("context_overlap_terms") or []),
                "similarity_checks": dict((generated.validation or {}).get("similarity_checks") or {}),
            },
            "writing_rules": get_writing_rule_snapshot(),
            "rule_source_hashes": dict(((generated.writing_rule_snapshot or {}).get("rule_source_hashes") or {})),
            "rule_source_paths": list(((generated.writing_rule_snapshot or {}).get("source_paths") or [])),
            "persona_id": (generated.persona_snapshot or {}).get("persona_id"),
            "persona_role": (generated.persona_snapshot or {}).get("default_role"),
            "case_style_applied": (generated.persona_snapshot or {}).get("case_style"),
            "persona": generated.persona_snapshot,
            "word_count": generated.word_count,
            "subreddit_policy": policy_metadata,
            "raw_response": generated.raw_response,
        }
        if retry_feedback:
            evidence["retry_feedback"] = retry_feedback
        return {
            "target_url": thread_url,
            "target_comment_url": None,
            "text": generated.text,
            "title": None,
            "body": None,
            "subreddit": subreddit,
            "user_flair_hint": policy_metadata.get("configured_user_flair"),
            "auto_user_flair": policy_metadata.get("auto_user_flair"),
            "discovered_target": candidate,
            "generation_evidence": evidence,
        }

    async def _build_generated_reply_payload(self, program: Dict[str, Any], item: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        subreddit = str(candidate.get("subreddit") or item.get("subreddit") or "").strip()
        profile_name = str(item.get("profile_name") or "")
        keywords = self._keywords_for_subreddit(program, subreddit=subreddit)
        style_samples = await self._style_samples_for_subreddit(program, subreddit=subreddit, keywords=keywords)
        conversation_context = await self._conversation_context_for_comment_target(candidate)
        recent_texts = list(program.get("generated_text_history") or [])
        same_thread_texts = self._generated_text_scope(
            program,
            local_date=str(item.get("local_date") or ""),
            thread_url=str(candidate.get("thread_url") or ""),
        )
        same_profile_texts = self._generated_text_scope(program, profile_name=profile_name)
        generated = await self.content_generator.generate_reply(
            profile_name=profile_name,
            subreddit=subreddit,
            target_excerpt=str(candidate.get("body_excerpt") or ""),
            target_author=str(candidate.get("author") or ""),
            keywords=keywords,
            style_samples=style_samples,
            conversation_context=conversation_context,
            recent_texts=recent_texts,
            same_thread_texts=same_thread_texts,
            same_profile_texts=same_profile_texts,
        )
        if not generated.success:
            return {
                "error": str(generated.error or "generated reddit reply failed"),
                "failure_class": "generation_failed",
                "retryable": True,
                "consume_attempt": False,
            }
        evidence = {
            "kind": "reply_comment",
            "subreddit": subreddit,
            "text": generated.text,
            "combined_text": generated.text,
            "style_summary": generated.style_summary,
            "conversation_summary": generated.conversation_summary,
            "conversation_samples": conversation_context,
            "sample_urls": generated.sample_urls,
            "validation": generated.validation,
            "policy_validation": generated.validation,
            "novelty_validation": {
                "nearby_duplicate": bool((generated.validation or {}).get("nearby_duplicate")),
                "context_overlap_terms": list((generated.validation or {}).get("context_overlap_terms") or []),
                "similarity_checks": dict((generated.validation or {}).get("similarity_checks") or {}),
            },
            "thread_url": candidate.get("thread_url"),
            "target_comment_url": candidate.get("target_comment_url"),
            "writing_rules": get_writing_rule_snapshot(),
            "rule_source_hashes": dict(((generated.writing_rule_snapshot or {}).get("rule_source_hashes") or {})),
            "rule_source_paths": list(((generated.writing_rule_snapshot or {}).get("source_paths") or [])),
            "persona_id": (generated.persona_snapshot or {}).get("persona_id"),
            "persona_role": (generated.persona_snapshot or {}).get("default_role"),
            "case_style_applied": (generated.persona_snapshot or {}).get("case_style"),
            "persona": generated.persona_snapshot,
            "word_count": generated.word_count,
            "subreddit_policy": self._subreddit_policy_metadata(
                program,
                subreddit=subreddit,
                profile_name=profile_name,
                action="reply_comment",
            ),
            "raw_response": generated.raw_response,
        }
        return {
            "text": generated.text,
            "generation_evidence": evidence,
        }

    async def _style_samples_for_subreddit(self, program: Dict[str, Any], *, subreddit: str, keywords: List[str]) -> List[Dict[str, Any]]:
        sample_count = max(1, int(_generation_config(program).get("style_sample_count", 3)))
        return await self._discover_posts_for_subreddit(
            subreddit=subreddit,
            keywords=keywords,
            max_posts=sample_count,
        )

    async def _conversation_context_for_subreddit(self, program: Dict[str, Any], *, subreddit: str, keywords: List[str]) -> List[Dict[str, Any]]:
        return await self._discover_posts_for_subreddit(
            subreddit=subreddit,
            keywords=keywords,
            max_posts=4,
        )

    async def _conversation_context_for_comment_target(self, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        samples: List[Dict[str, Any]] = [
            {
                "type": "thread_post",
                "target_url": candidate.get("thread_url"),
                "title": candidate.get("post_title"),
                "body_excerpt": candidate.get("post_body_excerpt"),
                "author": candidate.get("post_author"),
            },
            {
                "type": "target_comment",
                "target_comment_url": candidate.get("target_comment_url"),
                "excerpt": candidate.get("body_excerpt"),
                "author": candidate.get("author"),
            },
        ]
        if candidate.get("parent_excerpt"):
            samples.append(
                {
                    "type": "parent_comment",
                    "target_url": candidate.get("thread_url"),
                    "excerpt": candidate.get("parent_excerpt"),
                    "author": candidate.get("parent_author"),
                }
            )
        thread_url = str(candidate.get("thread_url") or "").strip()
        if thread_url:
            try:
                thread_payload = await self._thread_payload(thread_url)
            except Exception as exc:
                logger.warning(f"reddit thread context load failed for {thread_url}: {exc}")
                return samples
            comments_root = []
            if isinstance(thread_payload, list) and len(thread_payload) > 1:
                comments_root = (((thread_payload[1] or {}).get("data") or {}).get("children") or [])
            extras = self._walk_comment_nodes(
                comments_root,
                subreddit=str(candidate.get("subreddit") or ""),
                post_title=str(candidate.get("post_title") or ""),
                post_body_excerpt=str(candidate.get("post_body_excerpt") or ""),
                post_author=str(candidate.get("post_author") or ""),
                keywords=[],
            )
            target_comment_url = str(candidate.get("target_comment_url") or "").rstrip("/")
            for extra in extras:
                if str(extra.get("target_comment_url") or "").rstrip("/") == target_comment_url:
                    continue
                samples.append(
                    {
                        "type": "nearby_comment",
                        "target_comment_url": extra.get("target_comment_url"),
                        "excerpt": extra.get("body_excerpt"),
                        "author": extra.get("author"),
                    }
                )
                if len(samples) >= 4:
                    break
        return samples[:4]

    def _target_already_used(self, program: Dict[str, Any], *, profile_name: str, local_date: str, target_ref: str, exclude_work_item_id: Optional[str] = None) -> bool:
        allow_reuse = bool(_execution_policy(program).get("allow_target_reuse_within_day", False))
        if allow_reuse:
            return False
        normalized = _normalized_target_ref(target_ref)
        if not normalized:
            return False
        if normalized in self._reserved_target_refs(program, exclude_work_item_id=exclude_work_item_id):
            return True
        return normalized in self._cross_program_reserved_target_refs(program)

    def _blocked_subreddits_for_profile(self, program: Dict[str, Any], *, profile_name: str) -> set[str]:
        matrix = dict(program.get("community_block_matrix") or {})
        return {
            str(value).strip().lower()
            for value in list(matrix.get(profile_name) or [])
            if str(value).strip()
        }

    def _remember_community_block(self, program: Dict[str, Any], *, profile_name: str, subreddit: Optional[str]) -> None:
        normalized = str(subreddit or "").strip().lower()
        if not normalized:
            return
        matrix = dict(program.get("community_block_matrix") or {})
        blocked = [str(value).strip().lower() for value in list(matrix.get(profile_name) or []) if str(value).strip()]
        if normalized not in blocked:
            blocked.append(normalized)
        matrix[profile_name] = blocked
        program["community_block_matrix"] = matrix

    def _available_subreddits_for_profile(self, program: Dict[str, Any], *, profile_name: str, action: Optional[str] = None) -> List[str]:
        configured = [normalize_subreddit_name(value) for value in list(_program_filters(program).get("subreddits") or []) if normalize_subreddit_name(value)]
        if not configured:
            configured = [
                normalize_subreddit_name(value)
                for value in [
                    _subreddit_from_url(url)
                    for url in list(_program_filters(program).get("mandatory_join_urls") or [])
                ]
                if normalize_subreddit_name(value)
            ]
        blocked = self._blocked_subreddits_for_profile(program, profile_name=profile_name)
        available: List[str] = []
        for subreddit in configured:
            if subreddit.lower() in blocked:
                continue
            policy = _subreddit_policy(program, subreddit)
            if policy and not subreddit_profile_is_eligible(policy, profile_name=profile_name, action=action):
                continue
            available.append(subreddit)
        return available

    def _keywords_for_subreddit(self, program: Dict[str, Any], *, subreddit: Optional[str]) -> List[str]:
        base_keywords = [str(value).strip() for value in list(_program_filters(program).get("keywords") or []) if str(value).strip()]
        return subreddit_keywords(base_keywords, _subreddit_policy(program, subreddit))

    def _subreddit_policy_metadata(self, program: Dict[str, Any], *, subreddit: Optional[str], profile_name: Optional[str], action: Optional[str]) -> Dict[str, Any]:
        policy = _subreddit_policy(program, subreddit)
        if not policy:
            return {}
        return {
            "subreddit": policy.get("subreddit"),
            "allocation_weight": int(policy.get("allocation_weight", 1) or 1),
            "keyword_overrides": list(policy.get("keyword_overrides") or []),
            "auto_user_flair": subreddit_auto_user_flair_enabled(policy),
            "requires_user_flair": subreddit_requires_user_flair(policy, action),
            "configured_user_flair": profile_user_flair(policy, profile_name),
            "minimum_comment_karma": subreddit_minimum_comment_karma(policy, action),
            "blocked_warmup_stages": subreddit_blocked_warmup_stages(policy),
        }

    async def _profile_capability_error(
        self,
        program: Dict[str, Any],
        *,
        item: Dict[str, Any],
        session: RedditSession,
        actor_username: Optional[str],
    ) -> Optional[str]:
        action = str(item.get("action") or "").strip().lower()
        policy = _subreddit_policy(program, str(item.get("subreddit") or ""))
        if not policy:
            return None

        warmup_stage = str((session.get_warmup_state() or {}).get("stage") or "").strip().lower()
        blocked_stages = subreddit_blocked_warmup_stages(policy)
        if warmup_stage and warmup_stage in blocked_stages:
            return (
                f"reddit profile capability shortfall: warmup stage {warmup_stage} is blocked for "
                f"r/{policy.get('subreddit')}"
            )

        minimum_comment_karma = subreddit_minimum_comment_karma(policy, action)
        if minimum_comment_karma <= 0 or not actor_username:
            return None

        public_stats = await asyncio.to_thread(fetch_public_reddit_profile_stats, actor_username)
        comment_karma = int(public_stats.get("comment_karma") or 0)
        if comment_karma >= minimum_comment_karma:
            return None
        return (
            f"reddit profile capability shortfall: r/{policy.get('subreddit')} requires minimum comment karma "
            f"{minimum_comment_karma}, {actor_username} has {comment_karma}"
        )

    def _keyword_match(self, text_values: List[str], keywords: List[str]) -> bool:
        lowered = " ".join(str(value or "").lower() for value in text_values)
        if not keywords:
            return True
        return any(str(keyword).strip().lower() in lowered for keyword in keywords if str(keyword).strip())

    def _candidate_rank_score(self, program: Dict[str, Any], *, item: Dict[str, Any], candidate: Dict[str, Any]) -> int:
        local_date = str(item.get("local_date") or "")
        base_score = int(candidate.get("score") or 0) + int(candidate.get("comment_count") or 0)
        thread_url = str(candidate.get("thread_url") or candidate.get("target_url") or "").strip()
        usage = self._thread_usage_stats(
            program,
            local_date=local_date,
            thread_url=thread_url,
            exclude_work_item_id=str(item.get("id") or ""),
        )
        subreddit = str(candidate.get("subreddit") or "").strip().lower()
        target_author = str(candidate.get("author") or "").strip().lower()
        subreddit_usage = 0
        author_usage = 0
        for entry in list(program.get("target_history") or []):
            if str(entry.get("local_date") or "") != local_date:
                continue
            if subreddit and str(entry.get("subreddit") or "").strip().lower() == subreddit:
                subreddit_usage += 1
            if target_author and str(entry.get("target_author") or "").strip().lower() == target_author:
                author_usage += 1
        for work_item in ((program.get("compiled") or {}).get("work_items") or []):
            if str(work_item.get("id") or "") == str(item.get("id") or ""):
                continue
            if str(work_item.get("local_date") or "") != local_date:
                continue
            candidate_data = dict(work_item.get("discovered_target") or {})
            if subreddit and str(work_item.get("subreddit") or candidate_data.get("subreddit") or "").strip().lower() == subreddit:
                subreddit_usage += 1
            if target_author and str(candidate_data.get("author") or "").strip().lower() == target_author:
                author_usage += 1
        return base_score - (usage["count"] * 1000) - (subreddit_usage * 25) - (author_usage * 60)

    async def _fetch_json(self, url: str) -> Any:
        async with httpx.AsyncClient(headers=REDDIT_HTTP_HEADERS, follow_redirects=True, timeout=20.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def _discover_posts_for_subreddit(
        self,
        *,
        subreddit: str,
        keywords: List[str],
        max_posts: int,
    ) -> List[Dict[str, Any]]:
        cache_key = (str(subreddit).strip().lower(), tuple(sorted(str(value).strip().lower() for value in keywords if str(value).strip())), int(max_posts))
        cached = self._subreddit_post_cache.get(cache_key)
        if cached is not None:
            return [dict(entry) for entry in cached[:max_posts]]
        urls: List[str] = []
        if keywords:
            query = quote(" ".join(keywords))
            urls.append(
                f"https://www.reddit.com/r/{quote(subreddit)}/search/.json?raw_json=1&restrict_sr=1&sort=hot&t=month&q={query}"
            )
        urls.append(f"https://www.reddit.com/r/{quote(subreddit)}/hot/.json?raw_json=1&limit={max_posts}")

        ranked: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for url in urls:
            try:
                payload = await self._fetch_json(url)
            except Exception as exc:
                logger.warning(f"reddit post discovery failed for {subreddit}: {exc}")
                continue
            children = (((payload or {}).get("data") or {}).get("children") or [])
            for child in children:
                data = dict(child.get("data") or {})
                permalink = str(data.get("permalink") or "").strip()
                if not permalink:
                    continue
                if any(bool(data.get(flag)) for flag in ("locked", "archived", "stickied")):
                    continue
                title = str(data.get("title") or "").strip()
                body = str(data.get("selftext") or "").strip()
                if not self._keyword_match([title, body], keywords):
                    continue
                target_url = permalink if permalink.startswith("http") else f"https://www.reddit.com{permalink}"
                if target_url in seen:
                    continue
                seen.add(target_url)
                ranked.append(
                    {
                        "target_id": str(data.get("id") or target_url),
                        "target_url": target_url,
                        "subreddit": subreddit,
                        "title": title,
                        "body_excerpt": _short_text(body, 280),
                        "author": str(data.get("author") or "").strip(),
                        "score": int(data.get("score") or 0),
                        "comment_count": int(data.get("num_comments") or 0),
                        "locked": bool(data.get("locked")),
                        "archived": bool(data.get("archived")),
                        "source": "subreddit_search" if "search/.json" in url else "subreddit_hot",
                    }
                )
        ranked.sort(key=lambda value: (value.get("comment_count", 0), value.get("score", 0)), reverse=True)
        self._subreddit_post_cache[cache_key] = [dict(entry) for entry in ranked]
        return [dict(entry) for entry in ranked[:max_posts]]

    async def _thread_payload(self, thread_url: str) -> Any:
        normalized = str(thread_url or "").strip().rstrip("/")
        cached = self._thread_payload_cache.get(normalized)
        if cached is not None:
            return cached
        payload = await self._fetch_json(_thread_json_url(thread_url))
        self._thread_payload_cache[normalized] = payload
        return payload

    async def _thread_comment_candidates(self, *, post: Dict[str, Any], subreddit: str, keywords: List[str]) -> List[Dict[str, Any]]:
        thread_url = str(post.get("target_url") or "").strip()
        normalized = thread_url.rstrip("/")
        cached = self._thread_comment_cache.get(normalized)
        if cached is not None:
            return [dict(entry) for entry in cached]
        thread_payload = await self._thread_payload(thread_url)
        comments_root = []
        if isinstance(thread_payload, list) and len(thread_payload) > 1:
            comments_root = (((thread_payload[1] or {}).get("data") or {}).get("children") or [])
        candidates = self._walk_comment_nodes(
            comments_root,
            subreddit=subreddit,
            post_title=post["title"],
            post_body_excerpt=str(post.get("body_excerpt") or ""),
            post_author=str(post.get("author") or ""),
            keywords=keywords,
        )
        self._thread_comment_cache[normalized] = [dict(entry) for entry in candidates]
        return [dict(entry) for entry in candidates]

    async def _discover_post_target(self, program: Dict[str, Any], item: Dict[str, Any], *, actor_username: Optional[str] = None) -> Optional[Dict[str, Any]]:
        constraints = _program_filters(program)
        profile_name = str(item.get("profile_name") or "")
        local_date = str(item.get("local_date") or "")
        subreddits = self._candidate_subreddits_for_item(
            program,
            item=item,
            profile_name=profile_name,
            action=str(item.get("action") or ""),
        )

        explicit_targets = [str(value).strip() for value in list(constraints.get("explicit_post_targets") or []) if str(value).strip()]
        for target_url in explicit_targets:
            if self._target_already_used(program, profile_name=profile_name, local_date=local_date, target_ref=target_url, exclude_work_item_id=str(item.get("id") or "")):
                continue
            return {
                "target_id": target_url,
                "target_url": target_url,
                "source": "explicit_pool",
            }

        max_posts = max(1, int(_execution_policy(program).get("max_discovery_posts_per_subreddit", 6)))
        viable_candidates: List[Dict[str, Any]] = []
        for subreddit in subreddits:
            keywords = self._keywords_for_subreddit(program, subreddit=subreddit)
            ranked = await self._discover_posts_for_subreddit(
                subreddit=subreddit,
                keywords=keywords,
                max_posts=max_posts,
            )
            for candidate in ranked:
                target_url = str(candidate.get("target_url") or "")
                if self._target_already_used(program, profile_name=profile_name, local_date=local_date, target_ref=target_url, exclude_work_item_id=str(item.get("id") or "")):
                    continue
                realism_error = self._candidate_violates_realism(program, item=item, candidate=candidate, actor_username=actor_username)
                if realism_error:
                    continue
                candidate["ranking_score"] = self._candidate_rank_score(program, item=item, candidate=candidate)
                viable_candidates.append(candidate)
        viable_candidates.sort(
            key=lambda value: (
                int(value.get("ranking_score") or 0),
                int(value.get("comment_count") or 0),
                int(value.get("score") or 0),
            ),
            reverse=True,
        )
        return viable_candidates[0] if viable_candidates else None

    def _walk_comment_nodes(
        self,
        nodes: List[Dict[str, Any]],
        *,
        subreddit: str,
        post_title: str,
        post_body_excerpt: str,
        post_author: str,
        keywords: List[str],
        parent_author: Optional[str] = None,
        parent_excerpt: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        for child in list(nodes or []):
            if child.get("kind") != "t1":
                continue
            data = dict(child.get("data") or {})
            body = str(data.get("body") or "").strip()
            author = str(data.get("author") or "").strip()
            permalink = str(data.get("permalink") or "").strip()
            if not body or body in {"[deleted]", "[removed]"} or not author or author == "[deleted]" or not permalink:
                replies = data.get("replies")
                if isinstance(replies, dict):
                    collected.extend(
                        self._walk_comment_nodes(
                            replies.get("data", {}).get("children") or [],
                            subreddit=subreddit,
                            post_title=post_title,
                            post_body_excerpt=post_body_excerpt,
                            post_author=post_author,
                            keywords=keywords,
                            parent_author=author or parent_author,
                            parent_excerpt=body or parent_excerpt,
                        )
                    )
                continue
            target_comment_url = permalink if permalink.startswith("http") else f"https://www.reddit.com{permalink}"
            collected.append(
                {
                    "target_id": str(data.get("id") or target_comment_url),
                    "target_comment_url": target_comment_url,
                    "thread_url": None,
                    "subreddit": subreddit,
                    "author": author,
                    "body_excerpt": _short_text(body, 240),
                    "parent_author": parent_author,
                    "parent_excerpt": _short_text(parent_excerpt, 240),
                    "post_title": post_title,
                    "post_body_excerpt": _short_text(post_body_excerpt, 240),
                    "post_author": post_author,
                    "score": int(data.get("score") or 0),
                    "source": "thread_comment",
                }
            )
            replies = data.get("replies")
            if isinstance(replies, dict):
                collected.extend(
                    self._walk_comment_nodes(
                        replies.get("data", {}).get("children") or [],
                        subreddit=subreddit,
                        post_title=post_title,
                        post_body_excerpt=post_body_excerpt,
                        post_author=post_author,
                        keywords=keywords,
                        parent_author=author,
                        parent_excerpt=body,
                    )
                )
        return collected

    async def _discover_comment_target(self, program: Dict[str, Any], item: Dict[str, Any], *, actor_username: Optional[str] = None) -> Optional[Dict[str, Any]]:
        constraints = _program_filters(program)
        profile_name = str(item.get("profile_name") or "")
        local_date = str(item.get("local_date") or "")
        explicit_targets = [str(value).strip() for value in list(constraints.get("explicit_comment_targets") or []) if str(value).strip()]
        for target_comment_url in explicit_targets:
            if self._target_already_used(program, profile_name=profile_name, local_date=local_date, target_ref=target_comment_url, exclude_work_item_id=str(item.get("id") or "")):
                continue
            return {
                "target_id": target_comment_url,
                "target_comment_url": target_comment_url,
                "thread_url": None,
                "source": "explicit_pool",
            }

        subreddits = self._candidate_subreddits_for_item(
            program,
            item=item,
            profile_name=profile_name,
            action=str(item.get("action") or ""),
        )
        max_posts = max(1, int(_execution_policy(program).get("max_discovery_posts_per_subreddit", 6)))
        max_comments = max(1, int(_execution_policy(program).get("max_comment_candidates_per_post", 8)))
        viable_candidates: List[Dict[str, Any]] = []

        for subreddit in subreddits:
            keywords = self._keywords_for_subreddit(program, subreddit=subreddit)
            posts = await self._discover_posts_for_subreddit(
                subreddit=subreddit,
                keywords=keywords,
                max_posts=max_posts,
            )

            for post in posts:
                try:
                    candidates = await self._thread_comment_candidates(post=post, subreddit=subreddit, keywords=keywords)
                except Exception as exc:
                    logger.warning(f"reddit thread load failed for {post['target_url']}: {exc}")
                    continue
                filtered: List[Dict[str, Any]] = []
                for candidate in candidates:
                    target_ref = str(candidate.get("target_comment_url") or "")
                    if self._target_already_used(program, profile_name=profile_name, local_date=local_date, target_ref=target_ref, exclude_work_item_id=str(item.get("id") or "")):
                        continue
                    candidate["thread_url"] = post["target_url"]
                    realism_error = self._candidate_violates_realism(program, item=item, candidate=candidate, actor_username=actor_username)
                    if realism_error:
                        continue
                    candidate["ranking_score"] = self._candidate_rank_score(program, item=item, candidate=candidate)
                    filtered.append(candidate)
                    if len(filtered) >= max_comments:
                        break
                filtered.sort(
                    key=lambda value: (
                        int(value.get("ranking_score") or 0),
                        int(value.get("score") or 0),
                    ),
                    reverse=True,
                )
                viable_candidates.extend(filtered)
        viable_candidates.sort(
            key=lambda value: (
                int(value.get("ranking_score") or 0),
                int(value.get("score") or 0),
            ),
            reverse=True,
        )
        return viable_candidates[0] if viable_candidates else None


class RedditProgramScheduler:
    def __init__(
        self,
        *,
        store: RedditProgramStore,
        orchestrator: RedditProgramOrchestrator,
    ):
        self.store = store
        self.orchestrator = orchestrator
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._tick_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task and not self._task.done():
            logger.info("reddit program scheduler already running")
            return
        recovered = self.store.recover_interrupted_work()
        if recovered:
            logger.warning(f"reddit program scheduler recovered {len(recovered)} interrupted program(s)")
        self._stop = False
        self.store.update_scheduler_state(is_running=True, last_error=None)
        self._task = asyncio.create_task(self._loop())
        logger.info("reddit program scheduler started")

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.store.update_scheduler_state(is_running=False)
        logger.info("reddit program scheduler stopped")

    async def _loop(self) -> None:
        while not self._stop:
            try:
                await self.tick(source="loop")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"reddit program scheduler loop error: {exc}")
                self.store.update_scheduler_state(last_error=str(exc))
            await asyncio.sleep(60)

    async def tick(self, *, source: str = "manual") -> Dict[str, Any]:
        async with self._tick_lock:
            tick_at = _utc_iso()
            summary = {"processed": 0, "failed": 0}
            try:
                summary = await self.orchestrator.process_due_programs(max_programs=2)
                self.store.update_scheduler_state(
                    last_tick_at=tick_at,
                    last_error=None,
                    last_processed_count=int(summary.get("processed", 0)),
                )
            except Exception as exc:
                self.store.update_scheduler_state(last_tick_at=tick_at, last_error=str(exc))
                raise
            return {"source": source, "tick_at": tick_at, **summary}

    def get_status(self) -> Dict[str, Any]:
        programs = self.store.list_programs()
        counts: Dict[str, int] = {}
        for program in programs:
            status = str(program.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return {
            "scheduler": self.store.get_scheduler_state(),
            "counts": counts,
            "recent_programs": programs[:25],
        }
