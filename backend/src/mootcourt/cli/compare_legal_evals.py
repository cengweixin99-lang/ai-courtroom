from __future__ import annotations

import argparse
from pathlib import Path

from mootcourt.schemas.eval.legal_eval import (
    load_legal_eval_admission_policy,
    load_legal_eval_report,
)
from mootcourt.services.legal_eval_comparison import compare_legal_eval_reports


def _run(
    baseline_path: Path,
    candidate_path: Path,
    policy_path: Path,
    output_path: Path | None = None,
) -> bool:
    report = compare_legal_eval_reports(
        load_legal_eval_report(baseline_path),
        load_legal_eval_report(candidate_path),
        load_legal_eval_admission_policy(policy_path),
        baseline_path=baseline_path,
        candidate_path=candidate_path,
    )
    rendered = report.model_dump_json(indent=2)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return report.admitted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare BM25 and hybrid legal Eval reports against an admission policy"
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not _run(args.baseline, args.candidate, args.policy, args.output):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
