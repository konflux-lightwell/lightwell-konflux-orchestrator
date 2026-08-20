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

from import_orchestrator.ecosystems.base import Ecosystem


class _Dummy:
    name = "dummy"
    default_db_path = "./dummy.db"
    pipelinerun_prefix = "dummy-"

    def build_pipelinerun(self, ref, args):
        return {"ref": ref}

    def register_cli(self, subparsers):
        return None


def test_dummy_satisfies_ecosystem_protocol():
    assert isinstance(_Dummy(), Ecosystem)
