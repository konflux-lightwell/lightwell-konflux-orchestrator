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

from import_orchestrator.models import ImportItem, ImportStatus, PipelineRunStatus


class TestImportStatus:
    def test_values(self):
        assert ImportStatus.PENDING.value == "pending"
        assert ImportStatus.TRIGGERED.value == "triggered"
        assert ImportStatus.RUNNING.value == "running"
        assert ImportStatus.SUCCESS.value == "success"
        assert ImportStatus.FAILED.value == "failed"

    def test_is_string_enum(self):
        assert isinstance(ImportStatus.PENDING, str)
        assert ImportStatus.PENDING == "pending"

    def test_round_trip_from_value(self):
        for status in ImportStatus:
            assert ImportStatus(status.value) is status


class TestImportItem:
    def test_defaults(self):
        item = ImportItem(id=1, ref="quay.io/repo:tag@sha256:abc", status=ImportStatus.PENDING)
        assert item.pipelinerun_name is None
        assert item.triggered_at is None
        assert item.completed_at is None
        assert item.last_checked_at is None
        assert item.error_message is None
        assert item.retry_count == 0

    def test_all_fields(self):
        from datetime import datetime

        now = datetime.now()
        item = ImportItem(
            id=42,
            ref="quay.io/repo:tag@sha256:abc",
            status=ImportStatus.RUNNING,
            pipelinerun_name="pnc-import-abc",
            triggered_at=now,
            completed_at=None,
            last_checked_at=now,
            error_message=None,
            retry_count=2,
        )
        assert item.id == 42
        assert item.retry_count == 2
        assert item.pipelinerun_name == "pnc-import-abc"


class TestPipelineRunStatus:
    def test_running(self):
        pr = PipelineRunStatus(name="pr-1", status="Unknown")
        assert pr.is_running is True
        assert pr.is_successful is False
        assert pr.is_failed is False

    def test_successful(self):
        pr = PipelineRunStatus(name="pr-2", status="True")
        assert pr.is_running is False
        assert pr.is_successful is True
        assert pr.is_failed is False

    def test_failed(self):
        pr = PipelineRunStatus(name="pr-3", status="False")
        assert pr.is_running is False
        assert pr.is_successful is False
        assert pr.is_failed is True
