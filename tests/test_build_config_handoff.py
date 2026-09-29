import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "tekton/pipelines/python-remediated-build/python-remediated-build.yaml"
SOURCE_TASK = ROOT / "tekton/tasks/source-to-sdist/0.1/source-to-sdist.yaml"
WHEEL_TASK = ROOT / "tekton/tasks/python-fromager-build-wheels/0.1/python-fromager-build-wheels.yaml"


class UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys instead of silently overwriting."""


def _construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _document(path):
    return yaml.load(path.read_text(), Loader=UniqueKeyLoader)


def _script_syntax_check(script):
    if script.startswith("#!/usr/bin/env python"):
        compile(script, "<embedded-task-script>", "exec")
        return
    result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_source_task_carries_resolved_build_inputs_in_trusted_artifact():
    task = _document(SOURCE_TASK)
    scripts = {step["name"]: step.get("script", "") for step in task["spec"]["steps"]}
    resolve = scripts["resolve-version-overrides"]
    create = scripts["build-deterministic-sdist"]

    assert "version-overrides.json" in resolve
    assert "backend-env.sh" in resolve
    assert "build-configs/.commit" in resolve
    assert "get-fromager-settings.py" in resolve
    assert "get-allowed-artifacts.py" in resolve
    assert "All build-config helper scripts used by these" in resolve
    assert "find /var/workdir/sdists-repo -type d -exec chmod 0777" in resolve
    assert "find /var/workdir/sdists-repo -type f -exec chmod 0644" in resolve
    assert "version-env.sh" in create
    assert "sdists-repo" in task["spec"]["steps"][-1]["args"][-1]
    _script_syntax_check(scripts["resolve-version-overrides"])


def test_wheel_task_uses_trusted_artifact_settings_and_backend_environment():
    task = _document(WHEEL_TASK)
    steps = {step["name"]: step for step in task["spec"]["steps"]}
    render_script = steps["render-build-config-settings"]["script"]
    build_script = steps["build-wheels"]["script"]
    verify_script = steps["verify-allowed-artifacts"]["script"]

    assert "get-fromager-settings.py" in render_script
    _script_syntax_check(render_script)
    _script_syntax_check(build_script)
    assert "/var/workdir/sdists-repo/build-configs" in render_script
    assert 'source "$BACKEND_ENV"' in build_script
    assert build_script.index('source "$BACKEND_ENV"') < build_script.index('build-wheels "${ARGS[@]}"')
    assert "get-allowed-artifacts.py" in verify_script
    _script_syntax_check(verify_script)
    assert "/var/workdir/sdists-repo/build-configs" in verify_script
    assert all(param["name"] != "BUILD_CONFIGS_REPO_URL" for param in task["spec"].get("params", []))
    assert all(workspace["name"] != "git-auth" for workspace in task["spec"].get("workspaces", []))


def test_pipeline_only_passes_build_config_and_auth_to_source_task():
    pipeline = _document(PIPELINE)
    tasks = {task["name"]: task for task in pipeline["spec"]["tasks"]}
    source_params = {param["name"] for param in tasks["source-to-sdist"].get("params", [])}
    wheel_params = {param["name"] for param in tasks["fromager-build-wheels"].get("params", [])}

    assert "BUILD_CONFIGS_REPO_URL" in source_params
    assert "BUILD_CONFIGS_REVISION" in source_params
    assert "BUILD_CONFIGS_REPO_URL" not in wheel_params
    assert "SDISTS_ARTIFACT" in wheel_params
    assert "git-auth" in {workspace["name"] for workspace in tasks["source-to-sdist"].get("workspaces", [])}
    assert "workspaces" not in tasks["fromager-build-wheels"]
