from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "agent-admission.yml"


def test_real_model_admission_workflow_requires_secret_and_redacts_artifact() -> None:
    """CI 门禁必须显式使用密钥，并且只能上传脱敏后的质量报告。"""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "${{ secrets.QWEN_API_KEY }}" in workflow
    assert "Verify release secret" in workflow
    assert "--redact-output" in workflow
    assert "artifacts/qwen-agent-admission-summary.json" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "pull_request:" not in workflow
    assert "release:" not in workflow
