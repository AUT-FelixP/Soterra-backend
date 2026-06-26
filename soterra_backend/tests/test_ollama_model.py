from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from soterra_backend.agent.local_agent import LocalOllamaAgentService
from soterra_backend.config import Settings
from soterra_backend.extraction_quality_gate import validate_extraction_quality
from soterra_backend.extractors.base import ExtractionRequest
from soterra_backend.extractors.ollama_model import OllamaModelExtractor
from soterra_backend.extractors.parsed_document import ParsedDocument, ParsedPage
from soterra_backend.models import (
    AgentChatMessage,
    AgentChatSession,
    AgentMemoryEntry,
    ExtractedFinding,
    ExtractionResult,
    RepositorySnapshot,
)


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        filename="inspection.pdf",
        content=b"%PDF",
        project_name="Kauri Apartments",
        site_name="Level 1",
        trade="Passive Fire",
    )


def _payload() -> dict:
    return {
        "project_name": "Kauri Apartments",
        "site_name": "Level 1",
        "address": None,
        "inspection_type": "Passive Fire Inspection",
        "trade": "Passive Fire",
        "inspector": "Inspector",
        "report_date": "2026-06-01",
        "summary": "The inspection found passive fire issues requiring close-out.",
        "overall_outcome": "Reviewing",
        "units": ["Level 1"],
        "findings": [
            {
                "title": "Fire collar missing",
                "description": "Fire collar is missing in the Level 1 riser.",
                "severity": "High",
                "category": "Passive Fire",
                "trade": "Passive Fire",
                "location": "Riser",
                "unit_label": "Level 1",
                "status": "Open",
                "recurrence_risk": 70,
                "project_name": None,
                "issue_title": None,
                "plain_english_summary": None,
                "level": None,
                "unit_or_area": None,
                "inspection_type": None,
                "root_cause": None,
                "required_fix": None,
                "evidence_required": [],
                "source_document": None,
                "source_page": 1,
                "source_quote": "Fire collar missing in riser.",
                "confidence": 0.88,
                "extraction_warnings": [],
            }
        ],
        "predicted_inspections": [],
    }


class FakeResponse:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.text = content

    def json(self) -> dict:
        return {"message": {"content": self.content}}


class OllamaModelTest(unittest.TestCase):
    def test_missing_ollama_gives_clear_error(self) -> None:
        extractor = OllamaModelExtractor(base_url="http://localhost:11434", model_id="qwen2.5:7b-instruct")
        parsed = ParsedDocument(full_text="failed fire collar", pages=[ParsedPage(page_number=1, text="failed fire collar")])
        with patch("soterra_backend.extractors.ollama_model.requests.post", side_effect=requests.ConnectionError("down")):
            with self.assertRaisesRegex(RuntimeError, "Could not reach Ollama"):
                extractor.extract(parsed_document=parsed, request=_request())

    def test_invalid_ollama_json_retries_then_fails_clearly(self) -> None:
        calls = []

        def fake_post(**kwargs):
            calls.append(kwargs)
            return FakeResponse("not-json")

        extractor = OllamaModelExtractor(base_url="http://localhost:11434", model_id="qwen2.5:7b-instruct")
        parsed = ParsedDocument(full_text="failed fire collar", pages=[ParsedPage(page_number=1, text="failed fire collar")])
        with patch("soterra_backend.extractors.ollama_model.requests.post", side_effect=lambda *args, **kwargs: fake_post(**kwargs)):
            with self.assertRaisesRegex(RuntimeError, "invalid extraction JSON"):
                extractor.extract(parsed_document=parsed, request=_request())
        self.assertEqual(len(calls), 2)

    def test_valid_ollama_json_is_parsed_into_extraction_result(self) -> None:
        extractor = OllamaModelExtractor(base_url="http://localhost:11434", model_id="qwen2.5:7b-instruct")
        parsed = ParsedDocument(full_text="failed fire collar", pages=[ParsedPage(page_number=1, text="failed fire collar")])
        with patch("soterra_backend.extractors.ollama_model.requests.post", return_value=FakeResponse(__import__("json").dumps(_payload()))):
            extraction = extractor.extract(parsed_document=parsed, request=_request())
        self.assertEqual(extraction.findings[0].title, "Fire collar missing")

    def test_ollama_json_missing_report_metadata_is_normalized_from_request(self) -> None:
        payload = _payload()
        payload["inspector"] = None
        payload["report_date"] = None
        payload["summary"] = None
        payload["overall_outcome"] = "Reinspection required before close-out."
        extractor = OllamaModelExtractor(base_url="http://localhost:11434", model_id="qwen2.5:7b-instruct")
        parsed = ParsedDocument(full_text="failed fire collar", pages=[ParsedPage(page_number=1, text="failed fire collar")])
        with patch("soterra_backend.extractors.ollama_model.requests.post", return_value=FakeResponse(__import__("json").dumps(payload))):
            extraction = extractor.extract(parsed_document=parsed, request=_request())

        self.assertEqual(extraction.inspector, "Unknown inspector")
        self.assertEqual(extraction.report_date, "Unknown")
        self.assertEqual(extraction.overall_outcome, "Reviewing")
        self.assertTrue(extraction.summary)

    def test_passed_items_are_removed_from_ollama_findings(self) -> None:
        payload = _payload()
        payload["findings"].append(
            {
                **payload["findings"][0],
                "title": "Fire door signage and smoke seal complete",
                "description": "PASSED - Fire door signage and smoke seal installation complete. No action required.",
                "severity": "Low",
                "source_quote": "PASSED - Fire door signage and smoke seal installation complete. No action required.",
            }
        )
        extractor = OllamaModelExtractor(base_url="http://localhost:11434", model_id="qwen2.5:7b-instruct")
        parsed = ParsedDocument(full_text="failed fire collar", pages=[ParsedPage(page_number=1, text="failed fire collar")])
        with patch("soterra_backend.extractors.ollama_model.requests.post", return_value=FakeResponse(__import__("json").dumps(payload))):
            extraction = extractor.extract(parsed_document=parsed, request=_request())

        self.assertEqual(len(extraction.findings), 1)
        self.assertEqual(extraction.findings[0].title, "Fire collar missing")

    def test_general_category_is_inferred_for_construction_findings(self) -> None:
        payload = _payload()
        payload["findings"][0]["category"] = "General"
        payload["findings"][0]["trade"] = "General"
        payload["findings"][0]["title"] = "Annular gap around cable tray fire stopping exceeds approved tolerance"
        payload["findings"][0]["description"] = "Remove incomplete sealant and reinstate the fire-rated system."
        extractor = OllamaModelExtractor(base_url="http://localhost:11434", model_id="qwen2.5:7b-instruct")
        parsed = ParsedDocument(full_text="annular gap exceeds tolerance", pages=[ParsedPage(page_number=1, text="annular gap exceeds tolerance")])
        with patch("soterra_backend.extractors.ollama_model.requests.post", return_value=FakeResponse(__import__("json").dumps(payload))):
            extraction = extractor.extract(parsed_document=parsed, request=_request())

        self.assertEqual(extraction.findings[0].category, "Passive Fire - Penetrations")
        self.assertEqual(extraction.findings[0].trade, "Passive Fire")

    def test_ollama_cloud_api_key_is_sent_as_bearer_header(self) -> None:
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(kwargs)
            return FakeResponse(__import__("json").dumps(_payload()))

        extractor = OllamaModelExtractor(
            base_url="https://ollama.com",
            model_id="gpt-oss:20b",
            api_key="test-key",
        )
        parsed = ParsedDocument(full_text="failed fire collar", pages=[ParsedPage(page_number=1, text="failed fire collar")])
        with patch("soterra_backend.extractors.ollama_model.requests.post", side_effect=fake_post):
            extractor.extract(parsed_document=parsed, request=_request())

        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(calls[0]["json"]["response_format"]["type"], "json_schema")
        self.assertTrue(calls[0]["json"]["response_format"]["json_schema"]["strict"])

    def test_settings_read_ollama_api_key_from_env(self) -> None:
        with patch("soterra_backend.config._load_env_file", return_value=None), patch.dict(
            "os.environ",
            {
                "SOTERRA_EXTRACTOR_MODE": "ollama_text",
                "SOTERRA_OLLAMA_BASE_URL": "https://ollama.com",
                "SOTERRA_OLLAMA_API_KEY": "test-key",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.soterra_ollama_base_url, "https://ollama.com")
        self.assertEqual(settings.soterra_ollama_api_key, "test-key")

    def test_quality_diagnostics_include_lengths_counts_and_warnings(self) -> None:
        extraction = ExtractionResult(
            project_name="Kauri Apartments",
            site_name="Level 1",
            inspection_type="Fire",
            trade="General",
            inspector="Inspector",
            report_date="2026-06-01",
            summary="The report contains construction issues that require review.",
            findings=[
                ExtractedFinding(title="Issue needs review", description="Issue needs review.", severity="Medium", confidence=0.4),
            ],
        )
        diagnostics = validate_extraction_quality(extraction, "issue text long enough " * 10)
        self.assertIn("raw_text_length", diagnostics)
        self.assertEqual(diagnostics["finding_count"], 1)
        self.assertEqual(diagnostics["low_confidence_count"], 1)
        self.assertTrue(diagnostics["quality_warnings"])


class FakeAgentRepository:
    def __init__(self) -> None:
        self.messages: list[AgentChatMessage] = []
        self.memory: list[AgentMemoryEntry] = []
        self.snapshot = RepositorySnapshot(
            projects=[{"id": "prj-1", "slug": "kauri-apartments", "name": "Kauri Apartments"}],
            documents=[
                {
                    "id": "rpt-1",
                    "project_name": "Kauri Apartments",
                    "project_slug": "kauri-apartments",
                    "site_name": "Level 1",
                    "source_filename": "inspection.pdf",
                    "inspection_type": "Fire",
                    "trade": "Fire",
                    "inspector": "Inspector",
                    "report_date": "2026-06-01",
                    "status": "Completed",
                    "summary": "Fire collar missing.",
                    "uploaded_at": "2026-06-01T00:00:00+00:00",
                }
            ],
            jobs=[],
            findings=[
                {
                    "id": "issue-1",
                    "document_id": "rpt-1",
                    "project_name": "Kauri Apartments",
                    "project_slug": "kauri-apartments",
                    "site_name": "Level 1",
                    "title": "Fire collar missing",
                    "description": "Fire collar missing in riser.",
                    "severity": "High",
                    "trade": "Fire",
                    "category": "Passive Fire",
                    "status": "Open",
                    "location": "Riser",
                    "unit_label": "Level 1",
                    "evidence_required": ["after photos"],
                    "created_at": "2026-06-01T00:00:00+00:00",
                }
            ],
            predicted_inspections=[],
        )

    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        self.tenant_id = tenant_id
        return self.snapshot

    def create_agent_chat_session(self, *, tenant_id: str, user_id: str, title: str | None = None):
        return AgentChatSession(id="ses-1", tenant_id=tenant_id, user_id=user_id, title=title, created_at="now", updated_at="now")

    def get_agent_chat_session(self, *, tenant_id: str, user_id: str, session_id: str):
        return AgentChatSession(id=session_id, tenant_id=tenant_id, user_id=user_id, title="Chat", created_at="now", updated_at="now")

    def list_agent_chat_messages(self, *, tenant_id: str, user_id: str, session_id: str, limit: int):
        return self.messages[-limit:]

    def add_agent_chat_message(self, *, tenant_id: str, user_id: str, session_id: str, role: str, content: str, tool_name=None, tool_payload_json=None):
        self.messages.append(
            AgentChatMessage(id=f"msg-{len(self.messages)}", session_id=session_id, tenant_id=tenant_id, user_id=user_id, role=role, content=content, created_at="now")
        )

    def list_agent_memory_entries(self, *, tenant_id: str, user_id: str, session_id: str, limit: int):
        return self.memory[-limit:]

    def add_agent_memory_entry(self, *, tenant_id: str, user_id: str, session_id: str, memory_type: str, content: str, payload_json: str):
        self.memory.append(
            AgentMemoryEntry(id=f"mem-{len(self.memory)}", tenant_id=tenant_id, user_id=user_id, session_id=session_id, memory_type=memory_type, content=content, payload_json=payload_json, created_at="now")
        )

    def soft_delete_agent_chat_session(self, *, tenant_id: str, user_id: str, session_id: str) -> bool:
        return True


class LocalAgentTest(unittest.TestCase):
    def test_agent_prompt_uses_fetched_tenant_data(self) -> None:
        def fake_generate(self, *, system_prompt: str, user_prompt: str, timeout_seconds=None):
            raise AssertionError("Open issue discovery should use the fast grounded response path.")

        repo = FakeAgentRepository()
        service = LocalOllamaAgentService(repo)
        with patch.object(OllamaModelExtractor, "generate_text", fake_generate):
            response = service.chat(tenant_id="ten-1", user_id="usr-1", role="member", message="What open issues need fixing?")

        self.assertIn("I found 1 matching open issue", response.answer)
        self.assertIn("Kauri Apartments", response.answer)
        self.assertIn("Fire collar missing", response.answer)
        self.assertIn("Fix:", response.answer)
        self.assertIn("Evidence: after photos", response.answer)
        self.assertEqual(repo.tenant_id, "ten-1")
        self.assertTrue(repo.memory)

    def test_agent_replaces_weak_open_issue_answer_with_action_plan(self) -> None:
        def fake_generate(self, *, system_prompt: str, user_prompt: str, timeout_seconds=None):
            return "**Issue Summary:** There are open issues.\n\n**Next Action:** Address each issue according to the summary."

        repo = FakeAgentRepository()
        service = LocalOllamaAgentService(repo)
        with patch.object(OllamaModelExtractor, "generate_text", fake_generate):
            response = service.chat(tenant_id="ten-1", user_id="usr-1", role="member", message="What open issues need fixing?")

        self.assertIn("Open issues: 1", response.answer)
        self.assertIn("Severity: High", response.answer)
        self.assertIn("Location: Riser", response.answer)
        self.assertIn("Evidence: after photos", response.answer)
        self.assertNotIn("**", response.answer)

    def test_agent_falls_back_when_ollama_times_out(self) -> None:
        def fake_generate(self, *, system_prompt: str, user_prompt: str, timeout_seconds=None):
            raise RuntimeError("Could not reach Ollama at https://ollama.com.")

        repo = FakeAgentRepository()
        service = LocalOllamaAgentService(repo)
        with patch.object(OllamaModelExtractor, "generate_text", fake_generate):
            response = service.chat(tenant_id="ten-1", user_id="usr-1", role="member", message="What open issues need fixing?")

        self.assertIn("Open issues: 1", response.answer)
        self.assertIn("Fire collar missing", response.answer)
        self.assertFalse(response.safety["external_model_used"])
        self.assertEqual(response.safety["provider"], "native_query")


if __name__ == "__main__":
    unittest.main()
