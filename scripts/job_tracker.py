#!/usr/bin/env python3
"""Deterministic SQLite tracker for VerityApply for BOSS."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = SCRIPT_DIR / "schema.sql"
CONFIRMED_APPLICATION_STATUSES = {"APPLIED_CONFIRMED", "CONTACT_CONFIRMED"}


def connect(db_path: str, create_parent: bool = False) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve()
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
    if not create_parent and not path.exists():
        raise SystemExit(f"database not found: {path}")
    if path.exists():
        os.chmod(path, 0o600)
    conn = sqlite3.connect(path)
    os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def read_payload(source: str) -> dict[str, Any]:
    if source == "-":
        value = json.load(sys.stdin)
    else:
        with Path(source).expanduser().open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    if not isinstance(value, dict):
        raise SystemExit("JSON payload must be an object")
    return value


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def require(payload: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if payload.get(key) in (None, "")]
    if missing:
        raise SystemExit(f"missing required field(s): {', '.join(missing)}")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def get_job(conn: sqlite3.Connection, job_key: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
    if row is None:
        raise SystemExit(f"unknown job_key: {job_key}")
    return row


def validate_authorization(
    conn: sqlite3.Connection,
    authorization_id: str,
    candidate_id: str,
    job_key: str,
    action: str,
    *,
    exclude_application_id: str | None = None,
    material_ids: list[str] | None = None,
) -> None:
    row = conn.execute(
        """
        SELECT *,
          CASE
            WHEN revoked_at IS NOT NULL THEN 'revoked'
            WHEN datetime(starts_at) > datetime('now') THEN 'not_started'
            WHEN expires_at IS NOT NULL AND datetime(expires_at) <= datetime('now') THEN 'expired'
            ELSE 'active'
          END AS time_status
        FROM authorizations WHERE id = ?
        """,
        (authorization_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"authorization not found: {authorization_id}")
    if row["candidate_id"] != candidate_id:
        raise SystemExit("authorization belongs to a different candidate")
    if row["time_status"] != "active":
        raise SystemExit(f"authorization is not active: {row['time_status']}")
    actions = json.loads(row["actions_json"])
    if action not in actions:
        raise SystemExit(f"authorization does not allow action: {action}")
    scope = json.loads(row["scope_json"])
    job_keys = scope.get("job_keys") if isinstance(scope, dict) else None
    if not isinstance(job_keys, list) or not job_keys:
        raise SystemExit("authorization has no explicit job_keys; refusing fail-open scope")
    if job_key not in job_keys:
        raise SystemExit("job_key is outside the authorization scope")
    if material_ids:
        approved_materials = json.loads(row["materials_json"])
        if not isinstance(approved_materials, list) or not approved_materials:
            raise SystemExit("authorization has no approved materials")
        outside = [item for item in material_ids if item not in approved_materials]
        if outside:
            raise SystemExit(f"material is outside the authorization scope: {outside[0]}")
    if action == "submit" and row["max_count"] is not None:
        params: list[Any] = [authorization_id]
        exclusion = ""
        if exclude_application_id:
            exclusion = " AND id != ?"
            params.append(exclude_application_id)
        used = conn.execute(
            """
            SELECT COUNT(*) FROM applications
            WHERE authorization_id = ?
              AND status IN ('SUBMITTED_UNCONFIRMED', 'APPLIED_CONFIRMED', 'CONTACT_CONFIRMED')
            """ + exclusion,
            params,
        ).fetchone()[0]
        if used >= row["max_count"]:
            raise SystemExit("authorization max_count has been reached")


def append_event(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    event_type: str,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    actor: str = "agent",
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO workflow_events(
          entity_type, entity_id, event_type, from_status, to_status,
          actor, reason, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            event_type,
            from_status,
            to_status,
            actor,
            reason,
            compact(payload or {}),
        ),
    )


def command_init(args: argparse.Namespace) -> None:
    conn = connect(args.db, create_parent=True)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        print(dump({"database": str(Path(args.db).expanduser().resolve()), "schema_version": version}))
    finally:
        conn.close()


def command_upsert_candidate(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "id")
    conn = connect(args.db)
    with conn:
        old = conn.execute("SELECT onboarding_status FROM candidates WHERE id = ?", (payload["id"],)).fetchone()
        conn.execute(
            """
            INSERT INTO candidates(id, display_name, profile_version, onboarding_status, profile_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              display_name = COALESCE(excluded.display_name, candidates.display_name),
              profile_version = excluded.profile_version,
              onboarding_status = excluded.onboarding_status,
              profile_json = excluded.profile_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                payload["id"],
                payload.get("display_name"),
                str(payload.get("profile_version", "1")),
                payload.get("onboarding_status", "IN_PROGRESS"),
                compact(payload.get("profile", {})),
            ),
        )
        append_event(
            conn,
            "candidate",
            payload["id"],
            "CANDIDATE_UPSERTED",
            from_status=old[0] if old else None,
            to_status=payload.get("onboarding_status", "IN_PROGRESS"),
            payload={"profile_version": str(payload.get("profile_version", "1"))},
        )
    print(dump({"candidate_id": payload["id"], "saved": True}))


def command_upsert_resume(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "id", "candidate_id", "version_label")
    conn = connect(args.db)
    with conn:
        conn.execute(
            """
            INSERT INTO resume_versions(
              id, candidate_id, version_label, file_path, file_sha256,
              approval_status, allowed_for_submission
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              version_label = excluded.version_label,
              file_path = COALESCE(excluded.file_path, resume_versions.file_path),
              file_sha256 = COALESCE(excluded.file_sha256, resume_versions.file_sha256),
              approval_status = excluded.approval_status,
              allowed_for_submission = excluded.allowed_for_submission
            """,
            (
                payload["id"], payload["candidate_id"], payload["version_label"],
                payload.get("file_path"), payload.get("file_sha256"),
                payload.get("approval_status", "PENDING"),
                1 if payload.get("allowed_for_submission", False) else 0,
            ),
        )
        append_event(conn, "resume", payload["id"], "RESUME_UPSERTED", payload={"approval_status": payload.get("approval_status", "PENDING")})
    print(dump({"resume_id": payload["id"], "saved": True}))


def command_upsert_fact(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "id", "candidate_id", "fact_type", "fact_text")
    conn = connect(args.db)
    with conn:
        if payload.get("source_resume_id"):
            resume = conn.execute(
                "SELECT candidate_id FROM resume_versions WHERE id = ?",
                (payload["source_resume_id"],),
            ).fetchone()
            if resume is None or resume["candidate_id"] != payload["candidate_id"]:
                raise SystemExit("source resume does not belong to candidate")
        conn.execute(
            """
            INSERT INTO candidate_facts(
              id, candidate_id, fact_type, fact_text, source_resume_id, source_location,
              confidence, verification_status, sensitivity, allowed_uses_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              fact_type = excluded.fact_type, fact_text = excluded.fact_text,
              source_resume_id = COALESCE(excluded.source_resume_id, candidate_facts.source_resume_id),
              source_location = COALESCE(excluded.source_location, candidate_facts.source_location),
              confidence = COALESCE(excluded.confidence, candidate_facts.confidence),
              verification_status = excluded.verification_status,
              sensitivity = excluded.sensitivity,
              allowed_uses_json = excluded.allowed_uses_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                payload["id"], payload["candidate_id"], payload["fact_type"], payload["fact_text"],
                payload.get("source_resume_id"), payload.get("source_location"), payload.get("confidence"),
                payload.get("verification_status", "UNVERIFIED"), payload.get("sensitivity", "NORMAL"),
                compact(payload.get("allowed_uses", [])),
            ),
        )
        append_event(conn, "fact", payload["id"], "FACT_UPSERTED", to_status=payload.get("verification_status", "UNVERIFIED"))
    print(dump({"fact_id": payload["id"], "saved": True}))


JOB_FIELDS = [
    "platform", "external_job_id", "source_type", "source_url", "company_raw",
    "company_normalized", "title", "location", "work_mode", "employment_type",
    "salary_text", "posted_or_updated_at", "jd_snapshot", "freshness_status",
    "authenticity_result", "duplicate_result", "hard_gate_result",
    "total_score", "decision_tier", "current_stage", "checked_at",
]


def command_upsert_job(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "job_key", "platform", "company_raw", "company_normalized", "title")
    values = [payload.get(field) for field in JOB_FIELDS]
    score_json = compact(payload.get("score_breakdown", {}))
    conn = connect(args.db)
    with conn:
        old = conn.execute("SELECT current_stage FROM jobs WHERE job_key = ?", (payload["job_key"],)).fetchone()
        columns = ", ".join(["job_key", *JOB_FIELDS, "score_breakdown_json"])
        marks = ", ".join(["?"] * (len(JOB_FIELDS) + 2))
        updates = ", ".join(
            [f"{field} = COALESCE(excluded.{field}, jobs.{field})" for field in JOB_FIELDS]
            + ["score_breakdown_json = excluded.score_breakdown_json", "updated_at = CURRENT_TIMESTAMP"]
        )
        conn.execute(
            f"INSERT INTO jobs({columns}) VALUES ({marks}) "
            f"ON CONFLICT(job_key) DO UPDATE SET {updates}",
            [payload["job_key"], *values, score_json],
        )
        append_event(
            conn,
            "job",
            payload["job_key"],
            "JOB_UPSERTED",
            from_status=old[0] if old else None,
            to_status=payload.get("current_stage"),
            payload={"decision_tier": payload.get("decision_tier"), "total_score": payload.get("total_score")},
        )
    print(dump({"job_key": payload["job_key"], "saved": True}))


def command_record_evidence(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "candidate_id", "job_key", "requirement_text", "evidence_type")
    evidence_id = payload.get("id") or new_id("evidence")
    conn = connect(args.db)
    with conn:
        job = get_job(conn, payload["job_key"])
        fact_id = payload.get("candidate_fact_id")
        if fact_id:
            fact = conn.execute(
                "SELECT candidate_id FROM candidate_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            if fact is None or fact["candidate_id"] != payload["candidate_id"]:
                raise SystemExit(f"candidate_fact_id does not belong to candidate: {fact_id}")
        conn.execute(
            """
            INSERT INTO job_evidence(
              id, candidate_id, job_id, requirement_text, evidence_type, candidate_fact_id,
              migration_note, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              candidate_id = excluded.candidate_id,
              requirement_text = excluded.requirement_text,
              evidence_type = excluded.evidence_type,
              candidate_fact_id = excluded.candidate_fact_id,
              migration_note = excluded.migration_note,
              confidence = excluded.confidence
            """,
            (
                evidence_id, payload["candidate_id"], job["id"], payload["requirement_text"],
                payload["evidence_type"], fact_id, payload.get("migration_note"),
                payload.get("confidence"),
            ),
        )
        append_event(conn, "job_evidence", evidence_id, "EVIDENCE_RECORDED", payload={"job_key": payload["job_key"], "evidence_type": payload["evidence_type"]})
    print(dump({"evidence_id": evidence_id, "saved": True}))


def command_check_duplicate(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    job = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (args.job_key,)).fetchone()
    result: dict[str, Any] = {"job_key": args.job_key, "job_exists": job is not None, "applications": []}
    if job is not None:
        params: list[Any] = [job["id"]]
        candidate_filter = ""
        if args.candidate_id:
            candidate_filter = " AND candidate_id = ?"
            params.append(args.candidate_id)
        rows = conn.execute(
            "SELECT id, candidate_id, attempt_no, status, submitted_at, verification_ref "
            f"FROM applications WHERE job_id = ?{candidate_filter} ORDER BY attempt_no",
            params,
        ).fetchall()
        result["applications"] = [dict(row) for row in rows]
    result["is_duplicate"] = bool(result["applications"])
    print(dump(result))


def command_authorize(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "candidate_id", "scope_type", "scope", "actions")
    allowed_scope_types = {"JOB", "NAMED_BATCH", "SCHEDULED_SCOPE"}
    allowed_actions = {"submit", "send_outreach", "upload_resume"}
    if payload["scope_type"] not in allowed_scope_types:
        raise SystemExit(f"unsupported scope_type: {payload['scope_type']}")
    if not isinstance(payload["scope"], dict):
        raise SystemExit("scope must be an object")
    job_keys = payload["scope"].get("job_keys")
    if not isinstance(job_keys, list) or not job_keys or not all(isinstance(v, str) and v for v in job_keys):
        raise SystemExit("authorization scope requires a non-empty job_keys list")
    if not isinstance(payload["actions"], list) or not payload["actions"]:
        raise SystemExit("actions must be a non-empty list")
    unknown_actions = set(payload["actions"]) - allowed_actions
    if unknown_actions:
        raise SystemExit(f"unsupported authorization action(s): {', '.join(sorted(unknown_actions))}")
    if payload["scope_type"] == "JOB" and len(job_keys) != 1:
        raise SystemExit("JOB authorization must contain exactly one job_key")
    if "submit" in payload["actions"]:
        materials = payload.get("materials")
        if not isinstance(materials, list) or not materials or not all(isinstance(v, str) and v for v in materials):
            raise SystemExit("submit authorization requires a non-empty materials list")
    auth_id = payload.get("id") or new_id("auth")
    conn = connect(args.db)
    with conn:
        conn.execute(
            """
            INSERT INTO authorizations(
              id, candidate_id, campaign_id, scope_type, scope_json, actions_json,
              materials_json, max_count, starts_at, expires_at, stop_conditions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?)
            """,
            (
                auth_id,
                payload["candidate_id"],
                payload.get("campaign_id"),
                payload["scope_type"],
                compact(payload["scope"]),
                compact(payload["actions"]),
                compact(payload.get("materials", [])),
                payload.get("max_count"),
                payload.get("starts_at"),
                payload.get("expires_at"),
                compact(payload.get("stop_conditions", [])),
            ),
        )
        append_event(conn, "authorization", auth_id, "AUTHORIZATION_CREATED", actor="user", payload={"scope_type": payload["scope_type"]})
    print(dump({"authorization_id": auth_id, "saved": True}))


def command_record_application(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "candidate_id", "job_key", "status")
    if payload["status"] in CONFIRMED_APPLICATION_STATUSES:
        require(payload, "authorization_id", "verification_ref")
    if payload["status"] == "SUBMITTED_UNCONFIRMED":
        require(payload, "authorization_id")
    app_id = payload.get("id") or new_id("app")
    conn = connect(args.db)
    with conn:
        job = get_job(conn, payload["job_key"])
        if payload["status"] in {"SUBMITTED_UNCONFIRMED", "APPLIED_CONFIRMED"}:
            require(payload, "resume_id")
            resume = conn.execute(
                """SELECT candidate_id, approval_status, allowed_for_submission
                   FROM resume_versions WHERE id = ?""",
                (payload["resume_id"],),
            ).fetchone()
            if resume is None or resume["candidate_id"] != payload["candidate_id"]:
                raise SystemExit("resume does not belong to candidate")
            if resume["approval_status"] != "APPROVED" or resume["allowed_for_submission"] != 1:
                raise SystemExit("resume is not approved and allowed for submission")
        if payload.get("material_id"):
            material = conn.execute(
                """SELECT candidate_id, job_id, approval_status
                   FROM tailored_materials WHERE id = ?""",
                (payload["material_id"],),
            ).fetchone()
            if material is None or material["candidate_id"] != payload["candidate_id"] or material["job_id"] != job["id"]:
                raise SystemExit("tailored material does not match candidate and job")
            if payload["status"] in {"SUBMITTED_UNCONFIRMED", "APPLIED_CONFIRMED"} and material["approval_status"] != "APPROVED":
                raise SystemExit("tailored material is not approved")
        if payload.get("authorization_id"):
            scoped_material_ids = [
                value for value in (payload.get("resume_id"), payload.get("material_id")) if value
            ]
            validate_authorization(
                conn, payload["authorization_id"], payload["candidate_id"],
                payload["job_key"], "submit", exclude_application_id=app_id,
                material_ids=scoped_material_ids,
            )
        old = conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()
        if old:
            conn.execute(
                """
                UPDATE applications SET authorization_id = COALESCE(?, authorization_id),
                  resume_id = COALESCE(?, resume_id), material_id = COALESCE(?, material_id),
                  status = ?,
                  submitted_at = CASE
                    WHEN ? IN ('SUBMITTED_UNCONFIRMED', 'APPLIED_CONFIRMED', 'CONTACT_CONFIRMED')
                      THEN COALESCE(?, submitted_at, CURRENT_TIMESTAMP)
                    ELSE submitted_at
                  END,
                  verification_type = COALESCE(?, verification_type),
                  verification_ref = COALESCE(?, verification_ref), notes = COALESCE(?, notes),
                  updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    payload.get("authorization_id"), payload.get("resume_id"), payload.get("material_id"),
                    payload["status"], payload["status"], payload.get("submitted_at"),
                    payload.get("verification_type"), payload.get("verification_ref"),
                    payload.get("notes"), app_id,
                ),
            )
        else:
            attempt_no = payload.get("attempt_no")
            if attempt_no is None:
                attempt_no = conn.execute(
                    "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM applications WHERE candidate_id = ? AND job_id = ?",
                    (payload["candidate_id"], job["id"]),
                ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO applications(
                  id, candidate_id, campaign_id, job_id, authorization_id, resume_id,
                  material_id, attempt_no, status, submitted_at, verification_type,
                  verification_ref, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                  CASE WHEN ? IN ('SUBMITTED_UNCONFIRMED', 'APPLIED_CONFIRMED', 'CONTACT_CONFIRMED')
                    THEN COALESCE(?, CURRENT_TIMESTAMP) ELSE ? END,
                  ?, ?, ?)
                """,
                (
                    app_id, payload["candidate_id"], payload.get("campaign_id"), job["id"],
                    payload.get("authorization_id"), payload.get("resume_id"), payload.get("material_id"),
                    attempt_no, payload["status"], payload["status"], payload.get("submitted_at"),
                    payload.get("submitted_at"),
                    payload.get("verification_type"), payload.get("verification_ref"), payload.get("notes"),
                ),
            )
        append_event(
            conn, "application", app_id, "APPLICATION_STATUS_RECORDED",
            from_status=old[0] if old else None, to_status=payload["status"],
            payload={"job_key": payload["job_key"], "verification_type": payload.get("verification_type")},
        )
    print(dump({"application_id": app_id, "status": payload["status"], "saved": True}))


def command_record_outreach(args: argparse.Namespace) -> None:
    payload = read_payload(args.json)
    require(payload, "candidate_id", "job_key", "message_text")
    delivery = payload.get("delivery_status", "NOT_SENT")
    if delivery == "CUSTOM_OUTREACH_CONFIRMED":
        require(payload, "authorization_id", "verification_ref")
        if payload.get("approval_status") != "APPROVED":
            raise SystemExit("CUSTOM_OUTREACH_CONFIRMED requires approval_status=APPROVED")
        requirements = payload.get("jd_requirements", [])
        fact_ids = payload.get("evidence_fact_ids", [])
        if not requirements or not fact_ids:
            raise SystemExit("confirmed custom outreach requires JD requirements and evidence fact IDs")
    message_id = payload.get("id") or new_id("msg")
    conn = connect(args.db)
    with conn:
        job = get_job(conn, payload["job_key"])
        if payload.get("authorization_id"):
            validate_authorization(
                conn, payload["authorization_id"], payload["candidate_id"],
                payload["job_key"], "send_outreach",
            )
        for fact_id in payload.get("evidence_fact_ids", []):
            fact = conn.execute(
                "SELECT candidate_id, verification_status, allowed_uses_json FROM candidate_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if fact is None or fact["candidate_id"] != payload["candidate_id"]:
                raise SystemExit(f"unknown evidence fact for candidate: {fact_id}")
            if delivery == "CUSTOM_OUTREACH_CONFIRMED" and fact["verification_status"] != "VERIFIED":
                raise SystemExit(f"outreach evidence fact is not VERIFIED: {fact_id}")
            if delivery == "CUSTOM_OUTREACH_CONFIRMED":
                allowed_uses = json.loads(fact["allowed_uses_json"])
                if "outreach" not in allowed_uses:
                    raise SystemExit(f"outreach use is not allowed for fact: {fact_id}")
        old = conn.execute("SELECT delivery_status FROM outreach_messages WHERE id = ?", (message_id,)).fetchone()
        if old:
            conn.execute(
                """
                UPDATE outreach_messages SET application_id = COALESCE(?, application_id),
                  authorization_id = COALESCE(?, authorization_id), message_text = ?,
                  jd_requirements_json = ?, evidence_fact_ids_json = ?, approval_status = ?,
                  delivery_status = ?, sent_at = COALESCE(?, sent_at),
                  verification_ref = COALESCE(?, verification_ref), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    payload.get("application_id"), payload.get("authorization_id"), payload["message_text"],
                    compact(payload.get("jd_requirements", [])), compact(payload.get("evidence_fact_ids", [])),
                    payload.get("approval_status", "DRAFT"), delivery, payload.get("sent_at"),
                    payload.get("verification_ref"), message_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO outreach_messages(
                  id, candidate_id, job_id, application_id, authorization_id, version,
                  message_text, jd_requirements_json, evidence_fact_ids_json,
                  approval_status, delivery_status, sent_at, verification_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id, payload["candidate_id"], job["id"], payload.get("application_id"),
                    payload.get("authorization_id"), payload.get("version", 1), payload["message_text"],
                    compact(payload.get("jd_requirements", [])), compact(payload.get("evidence_fact_ids", [])),
                    payload.get("approval_status", "DRAFT"), delivery, payload.get("sent_at"),
                    payload.get("verification_ref"),
                ),
            )
        append_event(
            conn, "outreach", message_id, "OUTREACH_STATUS_RECORDED",
            from_status=old[0] if old else None, to_status=delivery,
            payload={"job_key": payload["job_key"], "version": payload.get("version", 1)},
        )
    print(dump({"outreach_id": message_id, "delivery_status": delivery, "saved": True}))


def command_list(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    clauses = []
    params: list[Any] = []
    if args.candidate_id:
        clauses.append("a.candidate_id = ?")
        params.append(args.candidate_id)
    if args.status:
        clauses.append("a.status = ?")
        params.append(args.status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        """
        SELECT a.id, a.candidate_id, j.job_key, j.company_raw, j.title, j.platform,
          a.attempt_no, a.status, a.submitted_at, a.verification_ref, a.updated_at
        FROM applications a JOIN jobs j ON j.id = a.job_id
        """ + where + " ORDER BY a.updated_at DESC, a.id DESC",
        params,
    ).fetchall()
    print(dump([dict(row) for row in rows]))


def command_stats(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    params: list[Any] = []
    where = ""
    if args.candidate_id:
        where = " WHERE candidate_id = ?"
        params.append(args.candidate_id)
    statuses = conn.execute(
        f"SELECT status, COUNT(*) AS count FROM applications{where} GROUP BY status ORDER BY status",
        params,
    ).fetchall()
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    outreach = conn.execute(
        "SELECT delivery_status, COUNT(*) AS count FROM outreach_messages GROUP BY delivery_status ORDER BY delivery_status"
    ).fetchall()
    print(dump({
        "jobs": jobs,
        "applications": {row["status"]: row["count"] for row in statuses},
        "outreach": {row["delivery_status"]: row["count"] for row in outreach},
    }))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a SQLite database")
    init.add_argument("--db", required=True)
    init.set_defaults(func=command_init)

    candidate = sub.add_parser("upsert-candidate", help="create or update a candidate profile")
    candidate.add_argument("--db", required=True)
    candidate.add_argument("--json", required=True, help="JSON file or - for stdin")
    candidate.set_defaults(func=command_upsert_candidate)

    resume = sub.add_parser("upsert-resume", help="create or update a resume version")
    resume.add_argument("--db", required=True)
    resume.add_argument("--json", required=True, help="JSON file or - for stdin")
    resume.set_defaults(func=command_upsert_resume)

    fact = sub.add_parser("upsert-fact", help="create or update a candidate fact")
    fact.add_argument("--db", required=True)
    fact.add_argument("--json", required=True, help="JSON file or - for stdin")
    fact.set_defaults(func=command_upsert_fact)

    job = sub.add_parser("upsert-job", help="create or update a job")
    job.add_argument("--db", required=True)
    job.add_argument("--json", required=True, help="JSON file or - for stdin")
    job.set_defaults(func=command_upsert_job)

    evidence = sub.add_parser("record-evidence", help="map a job requirement to candidate evidence")
    evidence.add_argument("--db", required=True)
    evidence.add_argument("--json", required=True, help="JSON file or - for stdin")
    evidence.set_defaults(func=command_record_evidence)

    duplicate = sub.add_parser("check-duplicate", help="check prior applications for a job")
    duplicate.add_argument("--db", required=True)
    duplicate.add_argument("--job-key", required=True)
    duplicate.add_argument("--candidate-id")
    duplicate.set_defaults(func=command_check_duplicate)

    authorize = sub.add_parser("authorize", help="record a user authorization")
    authorize.add_argument("--db", required=True)
    authorize.add_argument("--json", required=True, help="JSON file or - for stdin")
    authorize.set_defaults(func=command_authorize)

    application = sub.add_parser("record-application", help="record or update an application attempt")
    application.add_argument("--db", required=True)
    application.add_argument("--json", required=True, help="JSON file or - for stdin")
    application.set_defaults(func=command_record_application)

    outreach = sub.add_parser("record-outreach", help="record or update a tailored outreach message")
    outreach.add_argument("--db", required=True)
    outreach.add_argument("--json", required=True, help="JSON file or - for stdin")
    outreach.set_defaults(func=command_record_outreach)

    listing = sub.add_parser("list", help="list applications")
    listing.add_argument("--db", required=True)
    listing.add_argument("--candidate-id")
    listing.add_argument("--status")
    listing.set_defaults(func=command_list)

    stats = sub.add_parser("stats", help="summarize tracked jobs, applications, and outreach")
    stats.add_argument("--db", required=True)
    stats.add_argument("--candidate-id")
    stats.set_defaults(func=command_stats)
    return parser


def main() -> None:
    os.umask(0o077)
    args = build_parser().parse_args()
    try:
        args.func(args)
    except sqlite3.IntegrityError as exc:
        raise SystemExit(f"integrity error: {exc}") from exc


if __name__ == "__main__":
    main()

