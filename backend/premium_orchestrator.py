"""
Premium run orchestration state machine.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import premium_actions
import premium_content
import premium_safety
from premium_store import PremiumStore
from queue_manager import near_duplicate_ratio
from premium_verify import (
    build_pass_matrix,
    evaluate_evidence_contract,
    evaluate_verification_state,
    validate_action_evidence,
)

logger = logging.getLogger("PremiumOrchestrator")


class PremiumOrchestrator:
    def __init__(
        self,
        store: PremiumStore,
        broadcast_update: Optional[Callable[[str, Dict], Any]] = None,
        actions_module=None,
        content_module=None,
        safety_module=None,
    ):
        self.store = store
        self.broadcast_update = broadcast_update
        self.actions = actions_module or premium_actions
        self.content = content_module or premium_content
        self.safety = safety_module or premium_safety
        self._lock = asyncio.Lock()

    async def _emit(self, event_type: str, data: Dict) -> None:
        if not self.broadcast_update:
            return
        try:
            await self.broadcast_update(event_type, data)
        except Exception as exc:
            logger.warning(f"broadcast failure ({event_type}): {exc}")

    def _queued_run_ids_for_profile(self, profile_name: str) -> set:
        target = str(profile_name or "").strip().lower()
        queued = self.store.list_runs(limit=500, status="queued")
        return {
            str(run.get("id"))
            for run in queued
            if str((run.get("run_spec") or {}).get("profile_name") or "").strip().lower() == target
        }

    async def _emit_dequeued_runs(self, *, profile_name: str, queued_before: set) -> None:
        if not queued_before:
            return
        target = str(profile_name or "").strip().lower()
        scheduled = self.store.list_runs(limit=500, status="scheduled")
        for run in scheduled:
            run_id = str(run.get("id") or "")
            if run_id not in queued_before:
                continue
            run_profile = str((run.get("run_spec") or {}).get("profile_name") or "").strip().lower()
            if run_profile != target:
                continue
            await self._emit(
                "premium_run_dequeued",
                {
                    "run_id": run_id,
                    "profile_name": (run.get("run_spec") or {}).get("profile_name"),
                    "next_execute_at": run.get("next_execute_at"),
                },
            )

    def _build_precheck_evidence(
        self,
        *,
        run_id: str,
        cycle_index: int,
        profile_name: str,
        precheck: Dict,
    ) -> Dict:
        identity_check = dict(precheck.get("identity_check") or {})
        duplicate_precheck = dict(precheck.get("duplicate_precheck") or {})
        screenshot_urls = dict(precheck.get("screenshot_urls") or {})
        before = precheck.get("before_screenshot")
        after = precheck.get("after_screenshot")
        error = precheck.get("error")
        return {
            "action_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": run_id,
            "step_id": f"cycle_{cycle_index}_feed_precheck",
            "cycle_index": cycle_index,
            "action_type": "feed_precheck",
            "profile_name": profile_name,
            "target_url": precheck.get("profile_url"),
            "target_id": None,
            "before_screenshot": before,
            "after_screenshot": after,
            "screenshot_urls": {
                "before": screenshot_urls.get("before"),
                "after": screenshot_urls.get("after"),
            },
            "action_method": {
                "engine": "safety_precheck",
                "final_status": "completed" if precheck.get("success") else "blocked",
                "steps_count": 1,
                "action_trace": ["safety_precheck"],
                "selector_trace": [],
            },
            "result_state": {
                "success": bool(precheck.get("success")),
                "completed_count": 1 if precheck.get("success") else 0,
                "errors": [error] if error else [],
            },
            "confirmation": {
                "profile_identity_confirmed": bool(identity_check.get("passed")),
                "duplicate_precheck_passed": bool(duplicate_precheck.get("passed", True)),
            },
            "identity_check": identity_check,
            "duplicate_precheck": duplicate_precheck,
            "raw": {
                "checked_at": precheck.get("checked_at"),
                "error": error,
            },
        }

    async def process_due_runs(self, max_runs: int = 3) -> Dict:
        processed = 0
        failed = 0

        async with self._lock:
            due = self.store.get_due_cycles()
            if not due:
                return {"processed": 0, "failed": 0}

            seen_runs = set()
            for run_id, cycle_index, _ in due:
                if processed >= max_runs:
                    break
                if run_id in seen_runs:
                    continue
                seen_runs.add(run_id)

                try:
                    await self.process_cycle(run_id=run_id, cycle_index=cycle_index)
                    processed += 1
                except Exception as exc:
                    failed += 1
                    logger.error(f"premium run cycle failure {run_id}:{cycle_index}: {exc}")

        return {"processed": processed, "failed": failed}

    async def _fail_run(self, *, run_id: str, cycle_index: int, reason: str) -> None:
        self.store.set_cycle_status(run_id=run_id, cycle_index=cycle_index, status="failed", error=reason)
        run = self.store.get_run(run_id) or {}
        profile_name = str((run.get("run_spec") or {}).get("profile_name") or "")
        queued_before = self._queued_run_ids_for_profile(profile_name)
        pass_matrix = build_pass_matrix(run.get("verification_state", {}))
        self.store.set_run_status(run_id, "failed", error=reason, pass_matrix=pass_matrix)
        await self._emit_dequeued_runs(profile_name=profile_name, queued_before=queued_before)
        self.store.append_event(run_id, "run_failed", {"cycle_index": cycle_index, "reason": reason})
        await self._emit(
            "premium_run_failed",
            {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "error": reason,
                "pass_matrix": pass_matrix,
            },
        )

    @staticmethod
    def _contains_tunnel_connection_error(payload: Any) -> bool:
        if payload is None:
            return False
        if isinstance(payload, str):
            return "ERR_TUNNEL_CONNECTION_FAILED" in payload
        if isinstance(payload, dict):
            return any(PremiumOrchestrator._contains_tunnel_connection_error(v) for v in payload.values())
        if isinstance(payload, (list, tuple, set)):
            return any(PremiumOrchestrator._contains_tunnel_connection_error(v) for v in payload)
        return False

    @staticmethod
    def _collect_tunnel_errors(payload: Any) -> list[str]:
        matches: list[str] = []

        def _walk(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                if "ERR_TUNNEL_CONNECTION_FAILED" in value:
                    matches.append(value)
                return
            if isinstance(value, dict):
                for item in value.values():
                    _walk(item)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _walk(item)

        _walk(payload)
        return matches

    def _cycle_attempts(self, *, run_id: str, cycle_index: int) -> int:
        run = self.store.get_run(run_id) or {}
        for cycle in run.get("cycles", []):
            if int(cycle.get("index", -1)) == int(cycle_index):
                return int(cycle.get("attempts", 0))
        return 0

    def _history_duplicate_precheck(
        self,
        *,
        profile_name: str,
        caption: str,
        threshold: float,
        lookback_posts: int,
    ) -> Optional[Dict]:
        recent_posts = self.store.list_recent_feed_posts(
            profile_name=profile_name,
            limit=max(1, int(lookback_posts)),
        )
        if not recent_posts:
            return None

        top_similarity = 0.0
        matched_permalink = None
        for post in recent_posts:
            ratio = near_duplicate_ratio(str(caption or ""), str(post.get("text") or ""))
            if ratio > top_similarity:
                top_similarity = ratio
                matched_permalink = post.get("permalink")

        required_posts = max(1, int(lookback_posts))
        checked_posts = len(recent_posts)
        duplicate_block = top_similarity >= float(threshold)
        insufficient_posts = checked_posts < required_posts
        passed = (not duplicate_block) and (not insufficient_posts)

        return {
            "checked_posts": checked_posts,
            "threshold": float(threshold),
            "top_similarity": round(float(top_similarity), 4),
            "matched_post_permalink": matched_permalink if duplicate_block else None,
            "required_posts": required_posts,
            "insufficient_posts": bool(insufficient_posts),
            "history_limited": bool(insufficient_posts),
            "passed": bool(passed),
            "posts": recent_posts,
            "source": "historical_feed_evidence",
        }

    @staticmethod
    def _session_identity_fallback(*, profile_name: str, identity_check: Dict) -> Optional[Dict]:
        expected_avatar_ref = str(identity_check.get("profile_avatar_expected_ref") or "").strip()
        has_profile_hint = bool(identity_check.get("url_profile_hint"))
        if not has_profile_hint and not expected_avatar_ref:
            return None

        fallback = dict(identity_check or {})
        fallback["profile_name_seen"] = profile_name
        fallback["name_match"] = True
        fallback["passed"] = True
        fallback["fallback_source"] = "session_profile_hint"
        fallback["fallback_reason"] = "precheck_surface_unreachable"
        return fallback

    async def _defer_or_fail_on_tunnel_error(
        self,
        *,
        run_id: str,
        cycle_index: int,
        profile_name: str,
        action_key: str,
        action_result: Dict,
        max_cycle_deferrals: int,
        defer_delay_seconds: int,
    ) -> bool:
        if bool(action_result.get("success")):
            return False
        if not self._contains_tunnel_connection_error(action_result):
            return False

        attempts = self._cycle_attempts(run_id=run_id, cycle_index=cycle_index)
        tunnel_errors = self._collect_tunnel_errors(action_result)

        if attempts > max_cycle_deferrals:
            reason = (
                f"tunnel recovery exhausted during {action_key}: "
                f"attempts={attempts}, max_deferrals={max_cycle_deferrals}"
            )
            self.store.append_event(
                run_id,
                "cycle_tunnel_recovery_exhausted",
                {
                    "cycle_index": cycle_index,
                    "action": action_key,
                    "attempts": attempts,
                    "max_deferrals": max_cycle_deferrals,
                    "errors": tunnel_errors[:3],
                },
            )
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=reason)
            return True

        deferred = self.store.defer_cycle(
            run_id=run_id,
            cycle_index=cycle_index,
            delay_seconds=defer_delay_seconds,
            reason=f"transient tunnel outage during {action_key}",
            metadata={
                "action": action_key,
                "attempts": attempts,
                "max_deferrals": max_cycle_deferrals,
                "errors": tunnel_errors[:3],
            },
        )
        retry_at = (deferred or {}).get("scheduled_at")

        self.store.append_event(
            run_id,
            "cycle_deferred_transient",
            {
                "cycle_index": cycle_index,
                "action": action_key,
                "attempts": attempts,
                "max_deferrals": max_cycle_deferrals,
                "retry_at": retry_at,
                "delay_seconds": defer_delay_seconds,
                "errors": tunnel_errors[:3],
            },
        )
        await self._emit(
            "premium_step_result",
            {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "profile_name": profile_name,
                "action": action_key,
                "success": False,
                "deferred": True,
                "retry_at": retry_at,
                "attempts": attempts,
                "max_deferrals": max_cycle_deferrals,
                "error": f"transient tunnel outage during {action_key}; retry scheduled",
            },
        )
        return True

    async def _run_action_with_tunnel_retries(
        self,
        *,
        run_id: str,
        cycle_index: int,
        action_key: str,
        execute_action: Callable[[], Any],
        max_retries: int,
        action_timeout_seconds: int,
    ) -> Dict:
        attempt = 0
        timeout_seconds = max(1, int(action_timeout_seconds))
        while True:
            try:
                result = await asyncio.wait_for(execute_action(), timeout=float(timeout_seconds))
            except asyncio.TimeoutError:
                reason = f"{action_key} timed out after {timeout_seconds}s"
                self.store.append_event(
                    run_id,
                    "action_timeout",
                    {
                        "cycle_index": cycle_index,
                        "action": action_key,
                        "timeout_seconds": timeout_seconds,
                        "attempt": attempt + 1,
                    },
                )
                await self._emit(
                    "premium_step_result",
                    {
                        "run_id": run_id,
                        "cycle_index": cycle_index,
                        "action": action_key,
                        "success": False,
                        "completed_count": 0,
                        "expected_count": 0,
                        "error": reason,
                    },
                )
                return {
                    "success": False,
                    "completed_count": 0,
                    "expected_count": 0,
                    "error": "action_timeout",
                    "result": {
                        "final_status": "task_timeout",
                        "steps": [],
                        "errors": [reason],
                    },
                    "evidence": {
                        "action_method": {
                            "engine": "orchestrator_timeout_guard",
                            "final_status": "task_timeout",
                            "steps_count": 0,
                            "action_trace": [f"TIMEOUT {timeout_seconds}s"],
                            "selector_trace": [],
                        },
                        "result_state": {
                            "success": False,
                            "completed_count": 0,
                            "errors": [reason],
                        },
                    },
                }
            if not self._contains_tunnel_connection_error(result):
                return result
            if attempt >= max_retries:
                return result
            attempt += 1
            reason = f"transient tunnel error during {action_key}; retry {attempt}/{max_retries}"
            self.store.append_event(
                run_id,
                "action_retry_scheduled",
                {
                    "cycle_index": cycle_index,
                    "action": action_key,
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "reason": reason,
                },
            )
            await self._emit(
                "premium_step_result",
                {
                    "run_id": run_id,
                    "cycle_index": cycle_index,
                    "action": action_key,
                    "success": False,
                    "completed_count": 0,
                    "expected_count": 0,
                    "error": reason,
                    "retry_scheduled": True,
                    "attempt": attempt,
                    "max_retries": max_retries,
                },
            )
            await asyncio.sleep(1.5)

    async def _record_action(
        self,
        *,
        run_id: str,
        cycle_index: int,
        action_key: str,
        run_spec: Dict,
        profile_name: str,
        post_kind: Optional[str],
        action_result: Dict,
        extra_evidence: Optional[Dict] = None,
    ) -> Dict:
        evidence = dict(action_result.get("evidence") or {})
        if extra_evidence:
            evidence.update(extra_evidence)
        evidence["verification_key"] = action_key

        # Backfill mandatory evidence fields when wrappers return partial payloads.
        evidence.setdefault("action_id", str(uuid.uuid4()))
        evidence.setdefault("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        evidence.setdefault("run_id", run_id)
        evidence.setdefault("step_id", f"cycle_{cycle_index}_{action_key}")
        evidence.setdefault("cycle_index", cycle_index)
        evidence.setdefault("profile_name", profile_name)
        evidence.setdefault(
            "action_type",
            {
                "feed_posts": "feed_post",
                "group_posts": "group_post",
                "likes": "likes",
                "shares": "shares",
                "comment_replies": "comment_replies",
            }.get(action_key, action_key),
        )
        evidence.setdefault("confirmation", {})
        evidence.setdefault("action_method", {})
        if not isinstance(evidence.get("result_state"), dict):
            evidence["result_state"] = {}
        evidence["result_state"].setdefault("success", bool(action_result.get("success")))
        evidence["result_state"].setdefault("completed_count", int(action_result.get("completed_count", 0)))
        evidence["result_state"].setdefault("errors", [])

        contract = (run_spec or {}).get("verification_contract", {}) or {}
        evidence_validation = validate_action_evidence(
            evidence=evidence,
            action_key=action_key,
            run_id=run_id,
            expected_profile=profile_name,
            verification_contract=contract,
        )
        evidence["evidence_validation"] = evidence_validation
        registered_count = (
            int(action_result.get("completed_count", 0))
            if bool(evidence_validation.get("ok"))
            else 0
        )

        self.store.append_evidence(run_id, evidence)
        self.store.register_verification(
            run_id=run_id,
            key=action_key,
            count=registered_count,
            post_kind=post_kind,
            evidence=evidence,
        )

        run = self.store.get_run(run_id) or {}
        verification_state = run.get("verification_state", {})
        progress = {
            "run_id": run_id,
            "cycle_index": cycle_index,
            "pass_matrix": build_pass_matrix(verification_state),
            "observed": verification_state.get("observed", {}),
            "required": verification_state.get("required", {}),
        }
        await self._emit("premium_step_result", {
            "run_id": run_id,
            "cycle_index": cycle_index,
            "action": action_key,
            "success": bool(action_result.get("success")),
            "completed_count": int(action_result.get("completed_count", 0)),
            "registered_count": int(registered_count),
            "expected_count": int(action_result.get("expected_count", 0)),
            "error": action_result.get("error"),
            "evidence": evidence,
            "evidence_valid": evidence_validation.get("ok", False),
            "evidence_errors": evidence_validation.get("errors", []),
            "evidence_missing": evidence_validation.get("missing", []),
        })
        await self._emit("premium_verification_progress", progress)
        return {
            "ok": bool(evidence_validation.get("ok")),
            "validation": evidence_validation,
        }

    async def process_cycle(self, *, run_id: str, cycle_index: int) -> None:
        run = self.store.get_run(run_id)
        if not run:
            return

        status = run.get("status")
        if status not in ("scheduled", "in_progress"):
            return

        run_spec = run.get("run_spec", {})
        profile_name = run_spec.get("profile_name")
        if not profile_name:
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason="run_spec.profile_name missing")
            return

        if status == "scheduled":
            self.store.set_run_status(run_id, "in_progress")
            self.store.append_event(run_id, "run_started", {"profile_name": profile_name})
            await self._emit("premium_run_start", {
                "run_id": run_id,
                "profile_name": profile_name,
                "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })

        self.store.set_cycle_status(run_id=run_id, cycle_index=cycle_index, status="running")

        profile_config = self.store.get_profile_config(profile_name)
        if not profile_config:
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=f"premium config missing for {profile_name}")
            return

        execution_policy = profile_config.get("execution_policy", {})
        if not bool(execution_policy.get("enabled", True)):
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=f"premium execution disabled for {profile_name}")
            return
        max_action_retries = int(execution_policy.get("max_retries", 1))
        action_timeout_seconds = max(1, int(execution_policy.get("action_timeout_seconds", 420)))

        rules_snapshot = self.store.get_rules_snapshot()
        if not rules_snapshot:
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason="rules snapshot missing")
            return

        required_rules_version = profile_config.get("content_policy", {}).get("rules_snapshot_version")
        snapshot_version = rules_snapshot.get("version")
        if required_rules_version and required_rules_version != snapshot_version:
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=f"rules snapshot mismatch required={required_rules_version} current={snapshot_version}",
            )
            return

        cycle = None
        for c in (run.get("cycles") or []):
            if int(c.get("index", -1)) == int(cycle_index):
                cycle = c
                break
        if not cycle:
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason="cycle not found")
            return

        post_kind = str(cycle.get("post_kind", "character"))

        dedupe_precheck_enabled = bool(execution_policy.get("dedupe_precheck_enabled", True))
        dedupe_recent_feed_posts = int(execution_policy.get("dedupe_recent_feed_posts", 5))
        dedupe_threshold = float(execution_policy.get("dedupe_threshold", 0.90))
        block_on_duplicate = bool(execution_policy.get("block_on_duplicate", True))
        dedupe_retry_attempts = max(0, int(execution_policy.get("dedupe_retry_attempts", 2)))
        single_submit_guard = bool(execution_policy.get("single_submit_guard", True))
        generate_images_for_posts = bool(execution_policy.get("generate_images_for_posts", False))
        tunnel_recovery_cycles = max(0, int(execution_policy.get("tunnel_recovery_cycles", 2)))
        tunnel_recovery_delay_seconds = max(15, int(execution_policy.get("tunnel_recovery_delay_seconds", 90)))

        generation_attempt = 0
        bundle: Dict = {}
        caption = ""
        image_path = None
        precheck: Dict = {}
        identity_check: Dict = {}
        duplicate_precheck: Dict = {}
        profile_identity_confirmed = False

        while True:
            profile_config_current = self.store.get_profile_config(profile_name) or profile_config
            bundle = await self.content.generate_post_bundle(
                profile_name=profile_name,
                profile_config=profile_config_current,
                post_kind=post_kind,
                cycle_index=cycle_index + (generation_attempt * 101),
                rules_snapshot=rules_snapshot,
                require_image=generate_images_for_posts,
            )
            if not bundle.get("success"):
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=f"content generation failed: {bundle.get('error')}",
                )
                return

            caption = bundle.get("caption", "")
            image_path = bundle.get("image_path")

            precheck = await self.safety.run_feed_safety_precheck(
                profile_name=profile_name,
                caption=caption,
                lookback_posts=dedupe_recent_feed_posts,
                threshold=dedupe_threshold,
                run_id=run_id,
                cycle_index=cycle_index,
            )
            identity_check = dict(precheck.get("identity_check") or {})
            duplicate_precheck = dict(precheck.get("duplicate_precheck") or {})
            profile_identity_confirmed = bool(identity_check.get("passed"))

            if not profile_identity_confirmed:
                self.store.append_evidence(
                    run_id,
                    self._build_precheck_evidence(
                        run_id=run_id,
                        cycle_index=cycle_index,
                        profile_name=profile_name,
                        precheck=precheck,
                    ),
                )
                target_url = str(precheck.get("profile_url") or "")
                transient_identity_precheck = (
                    target_url.startswith("chrome-error://")
                    or self._contains_tunnel_connection_error(precheck)
                    or (
                        not str(identity_check.get("profile_name_seen") or "").strip()
                        and not bool(identity_check.get("profile_surface_detected"))
                        and int(duplicate_precheck.get("checked_posts", 0)) <= 0
                    )
                )
                if transient_identity_precheck:
                    fallback_duplicate = self._history_duplicate_precheck(
                        profile_name=profile_name,
                        caption=caption,
                        threshold=dedupe_threshold,
                        lookback_posts=dedupe_recent_feed_posts,
                    )
                    fallback_identity = self._session_identity_fallback(
                        profile_name=profile_name,
                        identity_check=identity_check,
                    )
                    fallback_ready = bool(
                        fallback_identity
                        and fallback_duplicate
                        and bool(fallback_duplicate.get("passed"))
                    )
                    if fallback_ready:
                        identity_check = dict(fallback_identity)
                        duplicate_precheck = dict(fallback_duplicate)
                        profile_identity_confirmed = True
                        self.store.append_event(
                            run_id,
                            "identity_precheck_fallback_applied",
                            {
                                "cycle_index": cycle_index,
                                "identity_source": fallback_identity.get("fallback_source"),
                                "duplicate_source": fallback_duplicate.get("source"),
                                "checked_posts": fallback_duplicate.get("checked_posts"),
                                "top_similarity": fallback_duplicate.get("top_similarity"),
                            },
                        )
                        await self._emit(
                            "premium_identity_check_result",
                            {
                                "run_id": run_id,
                                "cycle_index": cycle_index,
                                "success": True,
                                "identity_check": identity_check,
                                "fallback_applied": True,
                                "duplicate_precheck": {
                                    "passed": fallback_duplicate.get("passed"),
                                    "checked_posts": fallback_duplicate.get("checked_posts"),
                                    "source": fallback_duplicate.get("source"),
                                },
                            },
                        )
                        break

                    attempts = self._cycle_attempts(run_id=run_id, cycle_index=cycle_index)
                    if attempts > tunnel_recovery_cycles:
                        self.store.append_event(
                            run_id,
                            "identity_precheck_unreachable_exhausted",
                            {
                                "cycle_index": cycle_index,
                                "attempts": attempts,
                                "max_deferrals": tunnel_recovery_cycles,
                                "identity_check": identity_check,
                                "duplicate_precheck": duplicate_precheck,
                                "target_url": target_url,
                            },
                        )
                        self.content.cleanup_generated_image(image_path)
                        await self._fail_run(
                            run_id=run_id,
                            cycle_index=cycle_index,
                            reason="identity_precheck_unreachable",
                        )
                        return

                    deferred = self.store.defer_cycle(
                        run_id=run_id,
                        cycle_index=cycle_index,
                        delay_seconds=tunnel_recovery_delay_seconds,
                        reason="identity_precheck_unreachable",
                        metadata={
                            "attempts": attempts,
                            "max_deferrals": tunnel_recovery_cycles,
                            "identity_check": identity_check,
                            "duplicate_precheck": duplicate_precheck,
                            "target_url": target_url,
                        },
                    )
                    retry_at = (deferred or {}).get("scheduled_at")
                    self.store.append_event(
                        run_id,
                        "identity_precheck_unreachable_deferred",
                        {
                            "cycle_index": cycle_index,
                            "attempts": attempts,
                            "max_deferrals": tunnel_recovery_cycles,
                            "retry_at": retry_at,
                            "identity_check": identity_check,
                            "target_url": target_url,
                        },
                    )
                    await self._emit(
                        "premium_identity_check_result",
                        {
                            "run_id": run_id,
                            "cycle_index": cycle_index,
                            "success": False,
                            "identity_check": identity_check,
                            "error": "identity_precheck_unreachable",
                            "retry_scheduled": True,
                            "deferred": True,
                            "retry_at": retry_at,
                        },
                    )
                    self.content.cleanup_generated_image(image_path)
                    return

                self.store.append_event(
                    run_id,
                    "identity_verification_failed",
                    {
                        "cycle_index": cycle_index,
                        "identity_check": identity_check,
                        "precheck_error": precheck.get("error"),
                    },
                )
                await self._emit(
                    "premium_identity_check_result",
                    {
                        "run_id": run_id,
                        "cycle_index": cycle_index,
                        "success": False,
                        "identity_check": identity_check,
                        "error": precheck.get("error") or "identity_verification_failed",
                    },
                )
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason="identity_verification_failed",
                )
                return

            duplicate_blocked = (
                dedupe_precheck_enabled
                and block_on_duplicate
                and not bool(duplicate_precheck.get("passed", True))
            )
            if not duplicate_blocked:
                break

            no_posts_available = (
                int(duplicate_precheck.get("checked_posts", 0)) <= 0
                or bool(duplicate_precheck.get("insufficient_posts"))
            )
            if no_posts_available:
                attempts = self._cycle_attempts(run_id=run_id, cycle_index=cycle_index)
                self.store.append_evidence(
                    run_id,
                    self._build_precheck_evidence(
                        run_id=run_id,
                        cycle_index=cycle_index,
                        profile_name=profile_name,
                        precheck=precheck,
                    ),
                )
                if attempts > tunnel_recovery_cycles:
                    self.store.append_event(
                        run_id,
                        "duplicate_precheck_no_posts_exhausted",
                        {
                            "cycle_index": cycle_index,
                            "attempts": attempts,
                            "max_deferrals": tunnel_recovery_cycles,
                            "duplicate_precheck": duplicate_precheck,
                        },
                    )
                    self.content.cleanup_generated_image(image_path)
                    await self._fail_run(
                        run_id=run_id,
                        cycle_index=cycle_index,
                        reason="duplicate_precheck_no_posts",
                    )
                    return

                deferred = self.store.defer_cycle(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    delay_seconds=tunnel_recovery_delay_seconds,
                    reason="duplicate_precheck_no_posts",
                    metadata={
                        "attempts": attempts,
                        "max_deferrals": tunnel_recovery_cycles,
                        "duplicate_precheck": duplicate_precheck,
                    },
                )
                retry_at = (deferred or {}).get("scheduled_at")
                self.store.append_event(
                    run_id,
                    "duplicate_precheck_no_posts_deferred",
                    {
                        "cycle_index": cycle_index,
                        "attempts": attempts,
                        "max_deferrals": tunnel_recovery_cycles,
                        "retry_at": retry_at,
                        "duplicate_precheck": duplicate_precheck,
                    },
                )
                await self._emit(
                    "premium_precheck_blocked",
                    {
                        "run_id": run_id,
                        "cycle_index": cycle_index,
                        "profile_name": profile_name,
                        "duplicate_precheck": duplicate_precheck,
                        "error": "duplicate_precheck_no_posts",
                        "retry_scheduled": True,
                        "deferred": True,
                        "retry_at": retry_at,
                    },
                )
                self.content.cleanup_generated_image(image_path)
                return

            if generation_attempt < dedupe_retry_attempts:
                retry_number = generation_attempt + 1
                self.store.append_evidence(
                    run_id,
                    self._build_precheck_evidence(
                        run_id=run_id,
                        cycle_index=cycle_index,
                        profile_name=profile_name,
                        precheck=precheck,
                    ),
                )
                self.store.append_event(
                    run_id,
                    "duplicate_precheck_retry_scheduled",
                    {
                        "cycle_index": cycle_index,
                        "attempt": retry_number,
                        "max_retries": dedupe_retry_attempts,
                        "caption": caption,
                        "duplicate_precheck": duplicate_precheck,
                    },
                )
                await self._emit(
                    "premium_precheck_blocked",
                    {
                        "run_id": run_id,
                        "cycle_index": cycle_index,
                        "profile_name": profile_name,
                        "duplicate_precheck": duplicate_precheck,
                        "error": precheck.get("error") or "duplicate_precheck_failed",
                        "retry_scheduled": True,
                        "attempt": retry_number,
                        "max_retries": dedupe_retry_attempts,
                    },
                )
                self.store.remember_recent_caption(profile_name, caption)
                self.content.cleanup_generated_image(image_path)
                generation_attempt += 1
                continue

            self.store.append_evidence(
                run_id,
                self._build_precheck_evidence(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    profile_name=profile_name,
                    precheck=precheck,
                ),
            )
            self.store.append_event(
                run_id,
                "duplicate_precheck_failed",
                {
                    "cycle_index": cycle_index,
                    "attempt": generation_attempt + 1,
                    "max_retries": dedupe_retry_attempts,
                    "duplicate_precheck": duplicate_precheck,
                    "precheck_error": precheck.get("error"),
                },
            )
            await self._emit(
                "premium_precheck_blocked",
                {
                    "run_id": run_id,
                    "cycle_index": cycle_index,
                    "profile_name": profile_name,
                    "duplicate_precheck": duplicate_precheck,
                    "error": precheck.get("error") or "duplicate_precheck_failed",
                },
            )
            self.content.cleanup_generated_image(image_path)
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason="duplicate_precheck_failed",
            )
            return

        await self._emit(
            "premium_identity_check_result",
            {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "success": True,
                "identity_check": identity_check,
                "duplicate_precheck": duplicate_precheck,
            },
        )

        # 1) feed post
        feed_result = await self._run_action_with_tunnel_retries(
            run_id=run_id,
            cycle_index=cycle_index,
            action_key="feed_posts",
            max_retries=max_action_retries,
            action_timeout_seconds=action_timeout_seconds,
            execute_action=lambda: self.actions.publish_feed_post(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                caption=caption,
                image_path=None,
                profile_identity_confirmed=profile_identity_confirmed,
                identity_check=identity_check,
                duplicate_precheck=duplicate_precheck,
                single_submit_guard=single_submit_guard,
            ),
        )
        if str(feed_result.get("error", "")).strip().lower() == "action_timeout":
            self.content.cleanup_generated_image(image_path)
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=f"feed_posts action timed out after {action_timeout_seconds}s",
            )
            return
        if await self._defer_or_fail_on_tunnel_error(
            run_id=run_id,
            cycle_index=cycle_index,
            profile_name=profile_name,
            action_key="feed_posts",
            action_result=feed_result,
            max_cycle_deferrals=tunnel_recovery_cycles,
            defer_delay_seconds=tunnel_recovery_delay_seconds,
        ):
            self.content.cleanup_generated_image(image_path)
            return
        feed_record = await self._record_action(
            run_id=run_id,
            cycle_index=cycle_index,
            action_key="feed_posts",
            run_spec=run_spec,
            profile_name=profile_name,
            post_kind=post_kind,
            action_result=feed_result,
            extra_evidence={
                "rules_validation": bundle.get("rules_validation"),
                "generated_caption": caption,
                "generated_post_kind": post_kind,
                "identity_check": identity_check,
                "duplicate_precheck": duplicate_precheck,
                "precheck_screenshot_urls": precheck.get("screenshot_urls") or {},
            },
        )
        if not feed_record.get("ok"):
            self.content.cleanup_generated_image(image_path)
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=f"feed evidence contract failed: {feed_record.get('validation')}",
            )
            return
        if not feed_result.get("success"):
            self.content.cleanup_generated_image(image_path)
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=feed_result.get("error") or "feed post failed",
            )
            return

        self.store.remember_recent_caption(profile_name, caption)

        # 2) group post
        group_cfg = run_spec.get("group_discovery", {})
        group_result = await self._run_action_with_tunnel_retries(
            run_id=run_id,
            cycle_index=cycle_index,
            action_key="group_posts",
            max_retries=max_action_retries,
            action_timeout_seconds=action_timeout_seconds,
            execute_action=lambda: self.actions.discover_group_and_publish(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                topic_seed=str(group_cfg.get("topic_seed", "menopause groups")),
                allow_join_new=bool(group_cfg.get("allow_join_new", True)),
                join_pending_policy=str(group_cfg.get("join_pending_policy", "try_next_group")),
                group_post_text=caption,
                image_path=None,
                profile_identity_confirmed=profile_identity_confirmed,
                identity_check=identity_check,
            ),
        )
        if str(group_result.get("error", "")).strip().lower() == "action_timeout":
            self.content.cleanup_generated_image(image_path)
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=f"group_posts action timed out after {action_timeout_seconds}s",
            )
            return
        if await self._defer_or_fail_on_tunnel_error(
            run_id=run_id,
            cycle_index=cycle_index,
            profile_name=profile_name,
            action_key="group_posts",
            action_result=group_result,
            max_cycle_deferrals=tunnel_recovery_cycles,
            defer_delay_seconds=tunnel_recovery_delay_seconds,
        ):
            self.content.cleanup_generated_image(image_path)
            return
        group_record = await self._record_action(
            run_id=run_id,
            cycle_index=cycle_index,
            action_key="group_posts",
            run_spec=run_spec,
            profile_name=profile_name,
            post_kind=None,
            action_result=group_result,
            extra_evidence={
                "rules_validation": bundle.get("rules_validation"),
                "generated_caption": caption,
                "identity_check": identity_check,
            },
        )
        if not group_record.get("ok"):
            self.content.cleanup_generated_image(image_path)
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=f"group evidence contract failed: {group_record.get('validation')}",
            )
            return
        if not group_result.get("success"):
            self.content.cleanup_generated_image(image_path)
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=group_result.get("error") or "group post failed",
            )
            return

        engagement = run_spec.get("engagement_recipe", {})
        group_context_url = str(
            ((group_result.get("result") or {}).get("final_url"))
            or ((group_result.get("evidence") or {}).get("target_url"))
            or "https://m.facebook.com/groups"
        )

        # 3) likes
        likes_target = int(engagement.get("likes_per_cycle", 0))
        if likes_target > 0:
            likes_result = await self._run_action_with_tunnel_retries(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="likes",
                max_retries=max_action_retries,
                action_timeout_seconds=action_timeout_seconds,
                execute_action=lambda: self.actions.perform_likes(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    profile_name=profile_name,
                    likes_count=likes_target,
                    start_url=group_context_url,
                    profile_identity_confirmed=profile_identity_confirmed,
                    identity_check=identity_check,
                ),
            )
            if str(likes_result.get("error", "")).strip().lower() == "action_timeout":
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=f"likes action timed out after {action_timeout_seconds}s",
                )
                return
            if await self._defer_or_fail_on_tunnel_error(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                action_key="likes",
                action_result=likes_result,
                max_cycle_deferrals=tunnel_recovery_cycles,
                defer_delay_seconds=tunnel_recovery_delay_seconds,
            ):
                self.content.cleanup_generated_image(image_path)
                return
            likes_record = await self._record_action(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="likes",
                run_spec=run_spec,
                profile_name=profile_name,
                post_kind=None,
                action_result=likes_result,
                extra_evidence={"identity_check": identity_check},
            )
            if not likes_record.get("ok"):
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=f"likes evidence contract failed: {likes_record.get('validation')}",
                )
                return
            if not likes_result.get("success"):
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=likes_result.get("error") or "likes action failed",
                )
                return

        # 4) shares
        shares_target = int(engagement.get("shares_per_cycle", 0))
        if shares_target > 0:
            shares_result = await self._run_action_with_tunnel_retries(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="shares",
                max_retries=max_action_retries,
                action_timeout_seconds=action_timeout_seconds,
                execute_action=lambda: self.actions.perform_shares(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    profile_name=profile_name,
                    shares_count=shares_target,
                    share_target=str(engagement.get("share_target", "own_feed")),
                    start_url=group_context_url,
                    profile_identity_confirmed=profile_identity_confirmed,
                    identity_check=identity_check,
                ),
            )
            if str(shares_result.get("error", "")).strip().lower() == "action_timeout":
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=f"shares action timed out after {action_timeout_seconds}s",
                )
                return
            if await self._defer_or_fail_on_tunnel_error(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                action_key="shares",
                action_result=shares_result,
                max_cycle_deferrals=tunnel_recovery_cycles,
                defer_delay_seconds=tunnel_recovery_delay_seconds,
            ):
                self.content.cleanup_generated_image(image_path)
                return
            shares_record = await self._record_action(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="shares",
                run_spec=run_spec,
                profile_name=profile_name,
                post_kind=None,
                action_result=shares_result,
                extra_evidence={"identity_check": identity_check},
            )
            if not shares_record.get("ok"):
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=f"shares evidence contract failed: {shares_record.get('validation')}",
                )
                return
            if not shares_result.get("success"):
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=shares_result.get("error") or "shares action failed",
                )
                return

        # 5) comment replies
        replies_target = int(engagement.get("replies_per_cycle", 0))
        reply_text = run_spec.get("metadata", {}).get("supportive_reply_text") or "sending support here, you are not alone in this."
        replies_result = {"success": True, "completed_count": 0, "expected_count": 0, "error": None}
        replies_record = {"ok": True, "validation": {"ok": True, "missing": [], "errors": []}}
        if replies_target > 0:
            replies_result = await self._run_action_with_tunnel_retries(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="comment_replies",
                max_retries=max_action_retries,
                action_timeout_seconds=action_timeout_seconds,
                execute_action=lambda: self.actions.perform_comment_replies(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    profile_name=profile_name,
                    replies_count=replies_target,
                    reply_text=reply_text,
                    start_url=group_context_url,
                    profile_identity_confirmed=profile_identity_confirmed,
                    identity_check=identity_check,
                ),
            )
            if str(replies_result.get("error", "")).strip().lower() == "action_timeout":
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=f"comment_replies action timed out after {action_timeout_seconds}s",
                )
                return
            if await self._defer_or_fail_on_tunnel_error(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                action_key="comment_replies",
                action_result=replies_result,
                max_cycle_deferrals=tunnel_recovery_cycles,
                defer_delay_seconds=tunnel_recovery_delay_seconds,
            ):
                self.content.cleanup_generated_image(image_path)
                return
            replies_record = await self._record_action(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="comment_replies",
                run_spec=run_spec,
                profile_name=profile_name,
                post_kind=None,
                action_result=replies_result,
                extra_evidence={"identity_check": identity_check},
            )
            if not replies_record.get("ok"):
                self.content.cleanup_generated_image(image_path)
                await self._fail_run(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    reason=f"reply evidence contract failed: {replies_record.get('validation')}",
                )
                return
        self.content.cleanup_generated_image(image_path)

        if not replies_result.get("success"):
            await self._fail_run(
                run_id=run_id,
                cycle_index=cycle_index,
                reason=replies_result.get("error") or "replies action failed",
            )
            return

        self.store.set_cycle_status(run_id=run_id, cycle_index=cycle_index, status="success")
        self.store.append_event(run_id, "cycle_completed", {"cycle_index": cycle_index})

        # Evaluate completion status
        updated_run = self.store.get_run(run_id) or {}
        pending = [c for c in updated_run.get("cycles", []) if c.get("status") == "pending"]
        if pending:
            return

        count_evaluation = evaluate_verification_state(updated_run.get("verification_state", {}))
        evidence_evaluation = evaluate_evidence_contract(
            run_id=run_id,
            run_spec=updated_run.get("run_spec", {}),
            evidence_items=updated_run.get("evidence", []),
        )
        pass_matrix = count_evaluation.get("pass_matrix", {})

        if count_evaluation.get("passed") and evidence_evaluation.get("passed"):
            queued_before = self._queued_run_ids_for_profile(profile_name)
            self.store.set_run_status(run_id, "completed", pass_matrix=pass_matrix)
            await self._emit_dequeued_runs(profile_name=profile_name, queued_before=queued_before)
            self.store.append_event(
                run_id,
                "run_completed",
                {
                    "pass_matrix": pass_matrix,
                    "evidence_validation": evidence_evaluation,
                },
            )
            await self._emit(
                "premium_run_complete",
                {
                    "run_id": run_id,
                    "pass_matrix": pass_matrix,
                    "evaluated_at": count_evaluation.get("evaluated_at"),
                    "evidence_evaluated_at": evidence_evaluation.get("evaluated_at"),
                },
            )
        else:
            queued_before = self._queued_run_ids_for_profile(profile_name)
            reason = (
                "verification contract not met: "
                f"count_missing={count_evaluation.get('missing', [])}; "
                f"evidence_missing={evidence_evaluation.get('missing', [])}; "
                f"invalid_evidence={evidence_evaluation.get('invalid_evidence', [])}"
            )
            self.store.set_run_status(run_id, "failed", error=reason, pass_matrix=pass_matrix)
            await self._emit_dequeued_runs(profile_name=profile_name, queued_before=queued_before)
            self.store.append_event(
                run_id,
                "run_failed",
                {
                    "reason": reason,
                    "count_missing": count_evaluation.get("missing", []),
                    "evidence_missing": evidence_evaluation.get("missing", []),
                    "invalid_evidence": evidence_evaluation.get("invalid_evidence", []),
                },
            )
            await self._emit(
                "premium_run_failed",
                {
                    "run_id": run_id,
                    "error": reason,
                    "pass_matrix": pass_matrix,
                    "missing": count_evaluation.get("missing", []),
                    "evidence_missing": evidence_evaluation.get("missing", []),
                    "invalid_evidence": evidence_evaluation.get("invalid_evidence", []),
                },
            )
