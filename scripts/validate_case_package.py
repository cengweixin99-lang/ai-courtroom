from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class ValidationError(Exception):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {path}: {exc}") from exc


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def ensure_unique(items: list[dict[str, Any]], label: str) -> set[str]:
    ids = [item["id"] for item in items]
    ensure(len(ids) == len(set(ids)), f"Duplicate {label} IDs found")
    return set(ids)


def ensure_refs(values: list[str], valid: set[str], label: str) -> None:
    missing = set(values) - valid
    ensure(not missing, f"Unknown {label} references: {sorted(missing)}")


def validate(package: Path) -> dict[str, int]:
    manifest = load_json(package / "manifest.json")
    for relative_path in manifest["files"]:
        ensure((package / relative_path).is_file(), f"Manifest file is missing: {relative_path}")

    case = load_json(package / "case.json")
    facts_data = load_json(package / "facts.json")
    evidence_data = load_json(package / "evidence.json")
    defendant_data = load_json(package / "participants" / "defendant.json")
    witnesses_data = load_json(package / "participants" / "witnesses.json")
    role_materials = load_json(package / "role_materials.json")
    legal_profile = load_json(package / "legal_profile.json")
    legal_sources_data = load_json(package / "legal_sources.json")
    legal_issues_data = load_json(package / "legal_issues.json")
    procedure_profile = load_json(package / "procedure_profile.json")
    review_manifest = load_json(package / "review_manifest.json")
    ground_truth = load_json(package / "author_only" / "ground_truth.json")

    package_ids = {
        case["id"],
        facts_data["case_id"],
        evidence_data["case_id"],
        defendant_data["case_id"],
        witnesses_data["case_id"],
        role_materials["case_id"],
        legal_profile["case_id"],
        legal_issues_data["case_id"],
        procedure_profile["case_id"],
        review_manifest["case_id"],
        ground_truth["case_id"],
    }
    ensure(package_ids == {manifest["package_id"]}, "Case IDs are inconsistent across files")

    facts = facts_data["facts"]
    evidence = evidence_data["evidence"]
    witnesses = witnesses_data["witnesses"]
    defendant = defendant_data["defendant"]

    ensure(10 <= len(facts) <= 15, "MVP requires 10-15 facts")
    ensure(8 <= len(evidence) <= 12, "MVP requires 8-12 evidence items")
    ensure(2 <= len(witnesses) <= 3, "MVP requires 2-3 witnesses")
    ensure(case["fictional"] is True, "Case must be explicitly marked fictional")
    ensure(case["jurisdiction"] == "PRC-SHANGHAI", "E0 forum region must be fixed to Shanghai")
    ensure(case["status"] == "DEVELOPMENT_READY", "Case is not ready for development")
    ensure(case["development_mode_allowed"] is True, "Development mode is not enabled")

    fact_ids = ensure_unique(facts, "fact")
    evidence_ids = ensure_unique(evidence, "evidence")
    witness_ids = ensure_unique(witnesses, "witness")
    ensure(set(case["witness_ids"]) == witness_ids, "Case witness list does not match witnesses")
    ensure(case["defendant_id"] == defendant["id"], "Case defendant ID does not match")

    statements = defendant["statements"] + [
        statement for witness in witnesses for statement in witness["statements"]
    ]
    statement_ids = ensure_unique(statements, "statement")

    for fact in facts:
        ensure_refs(fact["supporting_evidence_ids"], evidence_ids, f"evidence for {fact['id']}")
        ensure_refs(
            fact["contradicting_evidence_ids"], evidence_ids, f"evidence for {fact['id']}"
        )
        ensure_refs(
            fact["supporting_statement_ids"], statement_ids, f"statements for {fact['id']}"
        )

    allowed_roles = {"prosecution", "defense"}
    for item in evidence:
        ensure_refs(item["related_fact_ids"], fact_ids, f"facts for {item['id']}")
        ensure(
            set(item["available_to"]).issubset(allowed_roles),
            f"Evidence {item['id']} has an unknown role",
        )

    for participant in [defendant, *witnesses]:
        known_key = "known_fact_ids" if participant["id"] == defendant["id"] else "allowed_fact_ids"
        ensure_refs(participant[known_key], fact_ids, f"allowed facts for {participant['id']}")
        ensure_refs(
            participant["forbidden_fact_ids"], fact_ids, f"forbidden facts for {participant['id']}"
        )
        ensure(
            not set(participant[known_key]) & set(participant["forbidden_fact_ids"]),
            f"Participant {participant['id']} has overlapping allowed and forbidden facts",
        )
        participant_statement_ids = {item["id"] for item in participant["statements"]}
        expected_statement_ids = set(
            participant.get("statement_ids", participant.get("prior_statement_ids", []))
        )
        ensure(
            participant_statement_ids == expected_statement_ids,
            f"Statement list mismatch for {participant['id']}",
        )
        for statement in participant["statements"]:
            ensure_refs(
                statement["related_fact_ids"], fact_ids, f"facts for statement {statement['id']}"
            )

    material_ids = ensure_unique(role_materials["materials"], "role material")
    ensure(len(material_ids) == 2, "Exactly one private strategy document per side is required")
    for material in role_materials["materials"]:
        ensure(material["visibility"] == [material["role"]], f"Material {material['id']} leaks")
        ensure_refs(
            material["priority_evidence_ids"], evidence_ids, f"evidence for {material['id']}"
        )

    source_ids = ensure_unique(legal_sources_data["legal_sources"], "legal source")
    profile_source_ids = (
        legal_profile["substantive_source_ids"]
        + legal_profile["procedure_source_ids"]
        + legal_profile["evidence_rule_source_ids"]
    )
    ensure_refs(profile_source_ids, source_ids, "legal source")
    candidate_source_ids = legal_profile.get("candidate_substantive_source_ids", [])
    ensure_refs(candidate_source_ids, source_ids, "candidate legal source")
    source_by_id = {item["id"]: item for item in legal_sources_data["legal_sources"]}
    superseded_profile_sources = [
        source_id
        for source_id in profile_source_ids
        if source_by_id[source_id]["status"].startswith("superseded")
    ]
    ensure(
        not superseded_profile_sources,
        f"Legal profile uses superseded sources: {superseded_profile_sources}",
    )
    unapproved_profile_sources = [
        source_id
        for source_id in profile_source_ids
        if source_by_id[source_id]["review_status"] not in {"approved", "verified"}
    ]
    ensure(
        not unapproved_profile_sources,
        f"Legal profile uses unapproved sources: {unapproved_profile_sources}",
    )
    historical_source_ids = set(legal_profile.get("available_historical_official_source_ids", []))
    ensure_refs(list(historical_source_ids), source_ids, "historical legal source")
    ensure(
        not historical_source_ids & set(profile_source_ids),
        "Historical sources must not enter the current legal profile",
    )

    elements = legal_issues_data["elements"]
    element_ids = ensure_unique(elements, "legal element")
    legal_issue_ids = ensure_unique(legal_issues_data["legal_issues"], "legal issue")
    ensure(legal_issue_ids, "At least one legal issue is required")
    for issue in legal_issues_data["legal_issues"]:
        ensure_refs(issue["element_ids"], element_ids, f"elements for {issue['id']}")
        ensure_refs(issue["related_fact_ids"], fact_ids, f"facts for {issue['id']}")
        ensure_refs(issue["legal_source_ids"], source_ids, f"sources for {issue['id']}")
    for element in elements:
        ensure_refs(element["supporting_fact_ids"], fact_ids, f"facts for {element['id']}")
        ensure_refs(element["contradicting_fact_ids"], fact_ids, f"facts for {element['id']}")
        ensure_refs(element["legal_source_ids"], source_ids, f"sources for {element['id']}")

    disputed_issue_ids = {item["id"] for item in case["disputed_issues"]}
    ensure(
        disputed_issue_ids == set(legal_issues_data["disputed_issue_ids"]),
        "Disputed issues are inconsistent",
    )

    expected_stages = [
        "COURT_OPENING",
        "INDICTMENT_AND_DEFENDANT_STATEMENT",
        "COURT_INVESTIGATION",
        "PROSECUTION_EVIDENCE_AND_EXAMINATION",
        "DEFENSE_EVIDENCE_AND_EXAMINATION",
        "WITNESS_QUESTIONING",
        "COURT_DEBATE_PROSECUTION",
        "COURT_DEBATE_DEFENSE",
        "DEFENDANT_FINAL_STATEMENT",
        "LEGAL_ANALYSIS",
        "REVIEW",
        "COMPLETED",
    ]
    ensure(procedure_profile["stages"] == expected_stages, "Court stages differ from the PRD")

    ensure(
        ground_truth["classification"] == "AUTHOR_ONLY_NEVER_LOAD_AT_RUNTIME",
        "Ground truth classification is unsafe",
    )
    ensure(
        "author_only/ground_truth.json" in manifest["runtime_excluded_files"],
        "Ground truth is not excluded from runtime",
    )
    ensure(
        legal_profile["legal_conclusion_allowed"] is False,
        "Development profile cannot allow real-world legal conclusions",
    )
    ensure(
        case["legal_conclusion_allowed"] is False,
        "Development case cannot allow real-world legal conclusions",
    )
    ensure(review_manifest["approved_for_release"] is False, "Development package cannot be released")
    ensure(
        review_manifest["approved_for_development"] is True,
        "Case package has not been approved for development",
    )
    current_document = legal_sources_data["source_document"]
    ensure(
        current_document["review_status"] == "verified",
        "Current official source document must be verified",
    )
    ensure(
        current_document["direct_official_provenance_verified"] is True,
        "Current source must have direct official provenance",
    )
    ensure(
        current_document["official_web_content_verified"] is True,
        "Current reproduction must record the completed content comparison",
    )
    ensure(
        current_document["official_source_url"].startswith("https://flk.npc.gov.cn/detail?"),
        "Current source must use a stable National Laws Database detail URL",
    )
    # 不可变 PDF 快照是对外法律发布的前置条件；本开发包已明确禁止发布，
    # 因此 CI 仅校验可复核的官方记录元数据和条文快照，不将缺失的发布材料误判为开发失效。
    ensure(
        bool(current_document["stored_path"]) and bool(current_document["sha256"]),
        "Current official source must declare a snapshot path and SHA-256 for release verification",
    )
    for document in legal_sources_data.get("historical_official_source_documents", []):
        ensure(
            document["status"].startswith("superseded"),
            "Historical official document must be marked superseded",
        )

    return {
        "facts": len(facts),
        "evidence": len(evidence),
        "witnesses": len(witnesses),
        "statements": len(statements),
        "legal_sources": len(source_ids),
        "legal_elements": len(element_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an E0 case authoring package")
    parser.add_argument(
        "package",
        nargs="?",
        type=Path,
        default=Path("data/authoring/CASE-001"),
    )
    args = parser.parse_args()

    try:
        counts = validate(args.package.resolve())
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    summary = ", ".join(f"{name}={count}" for name, count in counts.items())
    print(f"VALID DEVELOPMENT PACKAGE: {summary}")
    print("Development may proceed; production release and real-world legal conclusions remain blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
