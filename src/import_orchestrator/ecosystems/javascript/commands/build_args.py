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

import argparse
import os
import sys

from import_orchestrator.ecosystems.javascript import config


def add_build_args(parser: argparse.ArgumentParser) -> None:
    """Attach shared javascript build CLI flags."""
    parser.add_argument(
        "--target",
        choices=list(config.TARGET_CONFIGS),
        default=os.environ.get("LIGHTWELL_JAVASCRIPT_TARGET", config.DEFAULT_TARGET),
        help=f"Build target (default: {config.DEFAULT_TARGET}, or LIGHTWELL_JAVASCRIPT_TARGET)",
    )
    parser.add_argument(
        "--source-git-url",
        default=os.environ.get("LIGHTWELL_JAVASCRIPT_SOURCE_GIT_URL", config.DEFAULT_SOURCE_GIT_URL),
        help="Recipe monorepo URL (default: calunga-javascript build-configs)",
    )
    parser.add_argument(
        "--source-revision",
        default=os.environ.get("LIGHTWELL_JAVASCRIPT_SOURCE_REVISION", config.DEFAULT_SOURCE_REVISION),
        help="Recipe monorepo git revision (default: main)",
    )
    parser.add_argument(
        "--builder-image",
        default=os.environ.get("LIGHTWELL_JAVASCRIPT_BUILDER_IMAGE", config.DEFAULT_BUILDER_IMAGE),
        help="Pinned npm-builder image reference",
    )
    parser.add_argument(
        "--task-build-npm-package-bundle",
        default=os.environ.get(
            "LIGHTWELL_JAVASCRIPT_TASK_BUILD_BUNDLE",
            config.DEFAULT_TASK_BUILD_NPM_PACKAGE_BUNDLE,
        ),
        help="Tekton bundle for build-npm-package-oci-ta",
    )
    parser.add_argument(
        "--task-assess-npm-compliance-bundle",
        default=os.environ.get(
            "LIGHTWELL_JAVASCRIPT_TASK_ASSESS_BUNDLE",
            config.DEFAULT_TASK_ASSESS_NPM_COMPLIANCE_BUNDLE,
        ),
        help="Tekton bundle for assess-npm-compliance-oci-ta",
    )
    parser.add_argument(
        "--tl-npm-registry-url",
        default=os.environ.get("LIGHTWELL_JAVASCRIPT_TL_NPM_REGISTRY_URL") or None,
        help=(
            "TL npm registry URL for assess-npm-compliance "
            "(default: from --target; or LIGHTWELL_JAVASCRIPT_TL_NPM_REGISTRY_URL)"
        ),
    )
    parser.add_argument(
        "--pulp-read-secret-name",
        default=os.environ.get(
            "LIGHTWELL_JAVASCRIPT_PULP_READ_SECRET_NAME",
            config.DEFAULT_PULP_READ_SECRET_NAME,
        ),
        help="K8s Secret name with Pulp read basic-auth for TL registry",
    )
    parser.add_argument(
        "--publish-version-suffix",
        default=None,
        metavar="SUFFIX",
        help=(
            "Have the recipes publish <version>-SUFFIX instead of <version> (e.g. lwci.0), "
            "to exercise re-versioning without editing the manifest. SCRATCH target only."
        ),
    )


def check_build_args(args: argparse.Namespace) -> bool:
    """Reject build flag combinations argparse cannot express; print the error to stderr."""
    suffix = getattr(args, "publish_version_suffix", None)
    target = getattr(args, "target", config.DEFAULT_TARGET)
    if suffix and not config.TARGET_CONFIGS[target].get("allow_publish_version_suffix", False):
        allowed = ", ".join(t for t, cfg in config.TARGET_CONFIGS.items() if cfg.get("allow_publish_version_suffix"))
        print(
            f"ERROR: --publish-version-suffix is not allowed for target {target} (allowed: {allowed})",
            file=sys.stderr,
        )
        return False
    return True
