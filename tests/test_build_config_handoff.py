import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "tekton/pipelines/python-build/python-build.yaml"


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


def test_python_pipeline_carries_manifest_and_index_provenance():
    spec = _document(PIPELINE)["spec"]
    tasks = {task["name"]: task for task in spec["tasks"]}
    wheels = {param["name"]: param["value"] for param in tasks["fromager-build-wheels"]["params"]}
    index = {param["name"]: param["value"] for param in tasks["attach-build-index"]["params"]}
    results = {result["name"]: result["value"] for result in spec["results"]}

    # Manifest publication needs storage, and the index must consume that exact handoff.
    assert wheels["ociStorage"] == "$(params.ociStorage)"
    assert wheels["ociArtifactExpiresAfter"] == "$(params.ociArtifactExpiresAfter)"
    assert index["MANIFEST_ARTIFACT"] == "$(tasks.fromager-build-wheels.results.MANIFEST_ARTIFACT)"
    assert index["SOURCE_ARTIFACT"] == "$(tasks.clone-lightwell-builds.results.SOURCE_ARTIFACT)"
    assert index["IMAGE_REF"] == "$(tasks.fromager-build-wheels.results.IMAGE_REF)"
    assert index["COMPUTED_VERSION"] == "$(tasks.source-to-sdist.results.COMPUTED_VERSION)"
    assert index["BUILD_TYPE"] == "$(params.BUILD_TYPE)"
    for result in ("BUILD_INDEX_ATTACHED_REF", "BUILD_INDEX_DIGEST"):
        assert results[result] == f"$(tasks.attach-build-index.results.{result})"


def test_python_pipeline_passes_execution_images_and_external_bundles():
    spec = _document(PIPELINE)["spec"]
    tasks = {task["name"]: task for task in spec["tasks"]}
    parameters = {param["name"]: param for param in spec["params"]}
    contracts = {
        "source-to-sdist": ("SOURCE_TO_SDIST_TASK_BUNDLE", "source-to-sdist", "PYTHON_BUILDER_IMAGE"),
        "fromager-build-wheels": ("BUILD_WHEELS_TASK_BUNDLE", "python-fromager-build-wheels", "PYTHON_BUILDER_IMAGE"),
        "attach-build-index": ("ATTACH_BUILD_INDEX_TASK_BUNDLE", "attach-build-index", "FATH_CUAN_IMAGE"),
    }
    for task_name, (bundle, name, image) in contracts.items():
        task = tasks[task_name]
        reference = {param["name"]: param["value"] for param in task["taskRef"]["params"]}
        inputs = {param["name"]: param["value"] for param in task["params"]}
        assert task["taskRef"]["resolver"] == "bundles"
        assert reference["name"] == name
        assert reference["kind"] == "task"
        assert "$(params." not in reference["bundle"]
        assert bundle not in parameters
        assert inputs[image] == f"$(params.{image})"
        assert parameters[image]["type"] == "string"


def test_python_pipeline_has_no_unbound_parameters_or_workspaces():
    spec = _document(PIPELINE)["spec"]
    parameters = {param["name"] for param in spec["params"]}
    references = set(re.findall(r"\$\(params\.([\w-]+)\)", yaml.safe_dump(spec)))
    assert references <= parameters

    workspaces = {workspace["name"]: workspace for workspace in spec["workspaces"]}
    assert set(workspaces) == {"git-auth"}
    tasks = {task["name"]: task for task in spec["tasks"]}
    for task in tasks.values():
        for binding in task.get("workspaces", []):
            assert binding["workspace"] in workspaces
        assert set(task.get("runAfter", [])) <= tasks.keys()
    for task_name in ("fromager-build-wheels", "attach-build-index"):
        assert "workspaces" not in tasks[task_name]
