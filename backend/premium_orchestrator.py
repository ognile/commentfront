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
from premium_store import PremiumStore
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
    ):
        self.store = store
        self.broadcast_update = broadcast_update
        self.actions = actions_module or premium_actions
        self.content = content_module or premium_content
        self._lock = asyncio.Lock()

    async def _emit(self, event_type: str, data: Dict) -> None:
        if not self.broadcast_update:
            return
        try:
            await self.broadcast_update(event_type, data)
        except Exception as exc:
            logger.warning(f"broadcast failure ({event_type}): {exc}")

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
        pass_matrix = build_pass_matrix(run.get("verification_state", {}))
        self.store.set_run_status(run_id, "failed", error=reason, pass_matrix=pass_matrix)
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

        self.store.append_evidence(run_id, evidence)
        self.store.register_verification(
            run_id=run_id,
            key=action_key,
            count=int(action_result.get("completed_count", 0)),
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

        bundle = await self.content.generate_post_bundle(
            profile_name=profile_name,
            profile_config=profile_config,
            post_kind=post_kind,
            cycle_index=cycle_index,
            rules_snapshot=rules_snapshot,
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

        # 1) feed post
        feed_result = await self.actions.publish_feed_post(
            run_id=run_id,
            cycle_index=cycle_index,
            profile_name=profile_name,
            caption=caption,
            image_path=image_path,
        )
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
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=feed_result.get("error") or "feed post failed")
            return

        # 2) group post
        group_cfg = run_spec.get("group_discovery", {})
        group_result = await self.actions.discover_group_and_publish(
            run_id=run_id,
            cycle_index=cycle_index,
            profile_name=profile_name,
            topic_seed=str(group_cfg.get("topic_seed", "menopause groups")),
            allow_join_new=bool(group_cfg.get("allow_join_new", True)),
            join_pending_policy=str(group_cfg.get("join_pending_policy", "try_next_group")),
            group_post_text=caption,
            image_path=image_path,
        )
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
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=group_result.get("error") or "group post failed")
            return

        engagement = run_spec.get("engagement_recipe", {})

        # 3) likes
        likes_target = int(engagement.get("likes_per_cycle", 0))
        if likes_target > 0:
            likes_result = await self.actions.perform_likes(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                likes_count=likes_target,
            )
            likes_record = await self._record_action(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="likes",
                run_spec=run_spec,
                profile_name=profile_name,
                post_kind=None,
                action_result=likes_result,
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
                await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=likes_result.get("error") or "likes action failed")
                return

        # 4) shares
        shares_target = int(engagement.get("shares_per_cycle", 0))
        if shares_target > 0:
            shares_result = await self.actions.perform_shares(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                shares_count=shares_target,
                share_target=str(engagement.get("share_target", "own_feed")),
            )
            shares_record = await self._record_action(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="shares",
                run_spec=run_spec,
                profile_name=profile_name,
                post_kind=None,
                action_result=shares_result,
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
                await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=shares_result.get("error") or "shares action failed")
                return

        # 5) comment replies
        replies_target = int(engagement.get("replies_per_cycle", 0))
        reply_text = run_spec.get("metadata", {}).get("supportive_reply_text") or "sending support here, you are not alone in this."
        replies_result = {"success": True, "completed_count": 0, "expected_count": 0, "error": None}
        replies_record = {"ok": True, "validation": {"ok": True, "missing": [], "errors": []}}
        if replies_target > 0:
            replies_result = await self.actions.perform_comment_replies(
                run_id=run_id,
                cycle_index=cycle_index,
                profile_name=profile_name,
                replies_count=replies_target,
                reply_text=reply_text,
            )
            replies_record = await self._record_action(
                run_id=run_id,
                cycle_index=cycle_index,
                action_key="comment_replies",
                run_spec=run_spec,
                profile_name=profile_name,
                post_kind=None,
                action_result=replies_result,
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
            await self._fail_run(run_id=run_id, cycle_index=cycle_index, reason=replies_result.get("error") or "replies action failed")
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
            self.store.set_run_status(run_id, "completed", pass_matrix=pass_matrix)
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
            reason = (
                "verification contract not met: "
                f"count_missing={count_evaluation.get('missing', [])}; "
                f"evidence_missing={evidence_evaluation.get('missing', [])}; "
                f"invalid_evidence={evidence_evaluation.get('invalid_evidence', [])}"
            )
            self.store.set_run_status(run_id, "failed", error=reason, pass_matrix=pass_matrix)
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
