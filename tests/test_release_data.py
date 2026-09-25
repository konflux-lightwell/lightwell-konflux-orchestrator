"""
Copyright (C) 2026 Lightwell

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from import_orchestrator.clients import GitError
from import_orchestrator.release_data import (
    ReleaseDataError,
    ReleaseDataResolver,
    application_of,
)

# ---------------------------------------------------------------------------
# Fixture builders: write minimal konflux-release-data trees on disk.
# ---------------------------------------------------------------------------

_TENANT = "tenants-config/cluster/stone-prod-p01/tenants"
_MANAGED = "config/stone-prod-p01.wcfb.p1"


def _write(root: Path, rel: str, docs: list[dict]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs))


def _release_plan(
    name: str,
    *,
    namespace: str = "lightwell-poc-tenant",
    application: str = "pnc-import-novel",
    target: str = "rhtap-releng-tenant",
    rpa_name: str | None = "pnc-import-java-pulp-novel-prod",
    auto_release: bool = True,
) -> dict:
    labels: dict = {}
    if rpa_name is not None:
        labels["release.appstudio.openshift.io/releasePlanAdmission"] = rpa_name
    if not auto_release:
        labels["release.appstudio.openshift.io/auto-release"] = "false"
    return {
        "apiVersion": "appstudio.redhat.com/v1alpha1",
        "kind": "ReleasePlan",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {"application": application, "target": target},
    }


def _rpa(
    name: str = "pnc-import-java-pulp-novel-prod",
    *,
    namespace: str = "rhtap-releng-tenant",
    applications: list[str] | None = None,
    origin: str = "lightwell-poc-tenant",
    policy: str = "mrrc-lightwell-poc-novel-prod",
    service_account: str = "release-pulp-lightwell-prod",
    pipeline: str = "pipelines/managed/slan-cuan-release/slan-cuan-release.yaml",
) -> dict:
    return {
        "apiVersion": "appstudio.redhat.com/v1alpha1",
        "kind": "ReleasePlanAdmission",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "applications": applications or ["pnc-import-novel"],
            "origin": origin,
            "policy": policy,
            "pipeline": {
                "serviceAccountName": service_account,
                "pipelineRef": {
                    "params": [{"name": "pathInRepo", "value": pipeline}],
                },
            },
            "data": {"intention": "production", "pulp": {"domain": "lightwell"}},
        },
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal release-data tree with one RP and its RPA."""
    _write(tmp_path, f"{_TENANT}/lightwell/rp.yaml", [_release_plan("pnc-import-java-pulp-novel-prod")])
    _write(tmp_path, f"{_MANAGED}/rhtap-releng/rpa.yaml", [_rpa()])
    return tmp_path


class TestApplicationOf:
    def test_reads_application_label(self):
        manifest = {"metadata": {"labels": {"appstudio.openshift.io/application": "pnc-import-novel"}}}
        assert application_of(manifest) == "pnc-import-novel"

    def test_missing_label_returns_none(self):
        assert application_of({"metadata": {"labels": {}}}) is None
        assert application_of({}) is None


class TestFindReleasePlan:
    def test_happy_path(self, repo: Path):
        rp = ReleaseDataResolver(repo).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        assert rp.name == "pnc-import-java-pulp-novel-prod"
        assert rp.rpa_name == "pnc-import-java-pulp-novel-prod"
        assert rp.auto_release is True

    def test_no_match_raises(self, repo: Path):
        with pytest.raises(ReleaseDataError, match="no auto-releasing ReleasePlan"):
            ReleaseDataResolver(repo).find_release_plan("does-not-exist", "lightwell-poc-tenant")

    def test_auto_release_false_is_skipped(self, tmp_path: Path):
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [_release_plan("rp1", auto_release=False)])
        with pytest.raises(ReleaseDataError, match="no auto-releasing ReleasePlan"):
            ReleaseDataResolver(tmp_path).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")

    def test_ambiguous_release_plans_raise(self, tmp_path: Path):
        _write(tmp_path, f"{_TENANT}/a/rp.yaml", [_release_plan("rp-a")])
        _write(tmp_path, f"{_TENANT}/b/rp.yaml", [_release_plan("rp-b")])
        with pytest.raises(ReleaseDataError, match="2 auto-releasing ReleasePlans"):
            ReleaseDataResolver(tmp_path).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")

    def test_auto_generated_is_ignored(self, tmp_path: Path):
        _write(tmp_path, f"{_TENANT}/lightwell/rp.yaml", [_release_plan("rp-real")])
        # A duplicate under auto-generated/ must not create ambiguity.
        _write(tmp_path, f"{_TENANT}/auto-generated/rp.yaml", [_release_plan("rp-real")])
        rp = ReleaseDataResolver(tmp_path).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        assert rp.name == "rp-real"


class TestFindRpa:
    def test_resolves_by_label(self, repo: Path):
        resolver = ReleaseDataResolver(repo)
        rp = resolver.find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        rpa = resolver.find_rpa(rp)
        assert rpa.name == "pnc-import-java-pulp-novel-prod"
        assert rpa.namespace == "rhtap-releng-tenant"
        assert rpa.policy == "mrrc-lightwell-poc-novel-prod"
        assert rpa.service_account == "release-pulp-lightwell-prod"
        assert rpa.pipeline.endswith("slan-cuan-release.yaml")

    def test_label_target_missing_raises(self, tmp_path: Path):
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [_release_plan("rp1")])
        # RPA exists but in the wrong namespace, so the label target does not resolve.
        _write(tmp_path, f"{_MANAGED}/y/rpa.yaml", [_rpa(namespace="some-other-ns")])
        resolver = ReleaseDataResolver(tmp_path)
        rp = resolver.find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        with pytest.raises(ReleaseDataError, match="was not found"):
            resolver.find_rpa(rp)

    def test_tuple_fallback_when_no_label(self, tmp_path: Path):
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [_release_plan("rp1", rpa_name=None)])
        _write(tmp_path, f"{_MANAGED}/y/rpa.yaml", [_rpa()])
        resolver = ReleaseDataResolver(tmp_path)
        rp = resolver.find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        rpa = resolver.find_rpa(rp)
        assert rpa.name == "pnc-import-java-pulp-novel-prod"

    def test_label_tuple_mismatch_warns_but_resolves(self, tmp_path: Path, capsys):
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [_release_plan("rp1")])
        # RPA is in the right namespace/name but lists a different application.
        _write(tmp_path, f"{_MANAGED}/y/rpa.yaml", [_rpa(applications=["some-other-app"])])
        resolver = ReleaseDataResolver(tmp_path)
        rp = resolver.find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        rpa = resolver.find_rpa(rp)
        assert rpa.name == "pnc-import-java-pulp-novel-prod"
        err = capsys.readouterr().err
        assert "mismatch" in err


class TestFindRpaTupleFallback:
    def test_no_tuple_match_raises(self, tmp_path: Path):
        # RP has no explicit RPA label and no RPA satisfies the (app, origin, target) tuple.
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [_release_plan("rp1", rpa_name=None)])
        resolver = ReleaseDataResolver(tmp_path)
        rp = resolver.find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        with pytest.raises(ReleaseDataError, match="no ReleasePlanAdmission matches"):
            resolver.find_rpa(rp)

    def test_ambiguous_tuple_match_raises(self, tmp_path: Path):
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [_release_plan("rp1", rpa_name=None)])
        _write(tmp_path, f"{_MANAGED}/a/rpa.yaml", [_rpa(name="rpa-a")])
        _write(tmp_path, f"{_MANAGED}/b/rpa.yaml", [_rpa(name="rpa-b")])
        resolver = ReleaseDataResolver(tmp_path)
        rp = resolver.find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        with pytest.raises(ReleaseDataError, match="ambiguous ReleasePlanAdmission"):
            resolver.find_rpa(rp)


class TestWarnOnTupleMismatch:
    def test_all_three_mismatches_reported(self, capsys):
        from import_orchestrator.release_data import (
            ReleasePlan,
            ReleasePlanAdmission,
            _warn_on_tuple_mismatch,
        )

        rp = ReleasePlan(
            name="rp1",
            namespace="origin-ns",
            application="app-a",
            target="target-ns",
            rpa_name="rpa1",
            auto_release=True,
        )
        rpa = ReleasePlanAdmission(
            name="rpa1",
            namespace="different-ns",
            applications=("app-b",),
            origin="different-origin",
            policy=None,
            service_account=None,
            pipeline=None,
            pulp=None,
            intention=None,
        )
        _warn_on_tuple_mismatch(rp, rpa)
        err = capsys.readouterr().err
        assert "application 'app-a' not in RPA applications" in err
        assert "RP target 'target-ns' != RPA namespace 'different-ns'" in err
        assert "RPA origin 'different-origin' != RP namespace 'origin-ns'" in err


class TestScanFallbacksAndMalformedDocs:
    def test_missing_tree_yields_nothing(self, tmp_path: Path):
        # An empty checkout (no tenant/managed trees) resolves to no ReleasePlans.
        with pytest.raises(ReleaseDataError, match="no auto-releasing ReleasePlan"):
            ReleaseDataResolver(tmp_path).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")

    def test_malformed_yaml_is_skipped(self, tmp_path: Path):
        path = tmp_path / _TENANT / "bad" / "rp.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        # `grep` matches the kind line, but the document is not valid YAML.
        path.write_text("kind: ReleasePlan\nmetadata: {name: x\n")
        with pytest.raises(ReleaseDataError, match="no auto-releasing ReleasePlan"):
            ReleaseDataResolver(tmp_path).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")

    def test_release_plan_without_application_is_skipped(self, tmp_path: Path):
        doc = {"kind": "ReleasePlan", "metadata": {"name": "rp1"}, "spec": {}}
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [doc])
        with pytest.raises(ReleaseDataError, match="no auto-releasing ReleasePlan"):
            ReleaseDataResolver(tmp_path).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")

    def test_rpa_without_name_is_skipped(self, tmp_path: Path):
        _write(tmp_path, f"{_TENANT}/x/rp.yaml", [_release_plan("rp1", rpa_name=None)])
        doc = {"kind": "ReleasePlanAdmission", "metadata": {}, "spec": {}}
        _write(tmp_path, f"{_MANAGED}/y/rpa.yaml", [doc])
        resolver = ReleaseDataResolver(tmp_path)
        rp = resolver.find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        with pytest.raises(ReleaseDataError, match="no ReleasePlanAdmission matches"):
            resolver.find_rpa(rp)

    def test_grep_unavailable_falls_back_to_rglob(self, repo: Path):
        with patch("import_orchestrator.release_data.subprocess.run", side_effect=OSError("no grep")):
            rp = ReleaseDataResolver(repo).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        assert rp.name == "pnc-import-java-pulp-novel-prod"

    def test_grep_error_code_falls_back_to_rglob(self, repo: Path):
        fake = subprocess.CompletedProcess(args=["grep"], returncode=2, stdout="", stderr="boom")
        with patch("import_orchestrator.release_data.subprocess.run", return_value=fake):
            rp = ReleaseDataResolver(repo).find_release_plan("pnc-import-novel", "lightwell-poc-tenant")
        assert rp.name == "pnc-import-java-pulp-novel-prod"


class TestHeadRevision:
    def test_returns_none_when_not_a_git_repo(self, repo: Path):
        assert ReleaseDataResolver(repo).head_revision() is None

    def test_returns_sha_for_git_repo(self, tmp_path: Path):
        def _git(*args: str) -> None:
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True)

        _git("init", "-q")
        _git("config", "user.email", "t@example.com")
        _git("config", "user.name", "T")
        (tmp_path / "f.txt").write_text("x")
        _git("add", ".")
        _git("commit", "-q", "-m", "init")
        sha = ReleaseDataResolver(tmp_path).head_revision()
        assert sha is not None
        assert len(sha) == 40


class TestEnsure:
    def test_explicit_path_must_exist(self, tmp_path: Path):
        missing = tmp_path / "nope"
        with pytest.raises(ReleaseDataError, match="not found"):
            ReleaseDataResolver.ensure(str(missing))

    def test_explicit_existing_path_returns_resolver(self, repo: Path):
        resolver = ReleaseDataResolver.ensure(str(repo))
        assert resolver.repo_path == repo

    def test_default_existing_path_returns_resolver(self, repo: Path, monkeypatch):
        monkeypatch.setattr("import_orchestrator.release_data.RELEASE_DATA_DEFAULT_PATH", str(repo))
        resolver = ReleaseDataResolver.ensure(None)
        assert resolver.repo_path == Path(repo)

    def test_default_missing_path_is_cloned(self, tmp_path: Path, monkeypatch, capsys):
        missing = tmp_path / "reference" / "konflux-release-data"
        monkeypatch.setattr("import_orchestrator.release_data.RELEASE_DATA_DEFAULT_PATH", str(missing))
        with patch("import_orchestrator.release_data.clients.clone") as mock_clone:
            resolver = ReleaseDataResolver.ensure(None)
        mock_clone.assert_called_once()
        assert resolver.repo_path == Path(missing)
        assert "Cloning konflux-release-data" in capsys.readouterr().err

    def test_default_clone_failure_raises(self, tmp_path: Path, monkeypatch):
        missing = tmp_path / "reference" / "konflux-release-data"
        monkeypatch.setattr("import_orchestrator.release_data.RELEASE_DATA_DEFAULT_PATH", str(missing))
        with patch("import_orchestrator.release_data.clients.clone", side_effect=GitError("boom")):
            with pytest.raises(ReleaseDataError, match="failed to clone konflux-release-data"):
                ReleaseDataResolver.ensure(None)
