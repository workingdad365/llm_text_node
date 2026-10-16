import importlib.util
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("llm_text_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
p = sys.modules["llm_text_test.providers"]
node = sys.modules["llm_text_test.node"]


class ProviderTests(unittest.TestCase):
    def setUp(self):
        p._catalog = None

    def generate(self, provider, **overrides):
        args = dict(provider=provider, api_key="test-key", model="test-model", text="input",
                    system_prompt="instruction", max_tokens=4096, timeout=120, reasoning="Minimum")
        return p.generate(**(args | overrides))

    def test_reasoning_optional_disabled(self):
        self.assertEqual(p.reasoning_options({"reasoning": {"mandatory": False}}, "Minimum"),
                         {"exclude": True, "enabled": False})

    def test_reasoning_required_lowest_not_api_order(self):
        for supported, expected in [(["high", "low", "medium"], "low"), (["none", "minimal"], "minimal"), (None, "minimal")]:
            with self.subTest(supported=supported):
                self.assertEqual(p.reasoning_options({"reasoning": {"mandatory": True, "supported_efforts": supported}}, "Minimum"),
                                 {"exclude": True, "effort": expected})

    def test_unknown_and_default_do_not_force_effort(self):
        for record, mode in [({}, "Minimum"), ({"reasoning": {"mandatory": True}}, "Minimum"),
                             ({"reasoning": {"mandatory": False}}, "Model default")]:
            self.assertEqual(p.reasoning_options(record, mode), {"exclude": True})

    @patch.object(p, "request_json")
    @patch.object(p, "openrouter_model", return_value={"reasoning": {"mandatory": False}})
    def test_openrouter_routing_and_reasoning(self, catalog, request):
        request.return_value = {"choices": [{"message": {"content": "answer", "reasoning": "hidden"}, "finish_reason": "stop"}]}
        result = self.generate("OpenRouter", model="vendor/model:free:nitro", routing_suffix=":floor", provider_slug="one, two")
        self.assertEqual(result, "answer")
        catalog.assert_called_once_with("vendor/model:free:nitro")
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["model"], "vendor/model:free:floor")
        self.assertEqual(body["provider"], {"only": ["one", "two"], "allow_fallbacks": False})
        self.assertEqual(body["reasoning"], {"exclude": True, "enabled": False})

    @patch.object(p, "request_json")
    def test_native_requests_and_text_only(self, request):
        fixtures = {
            "OpenAI": {"status": "completed", "output": [{"type": "reasoning", "summary": []}, {"type": "message", "content": [{"type": "output_text", "text": "answer"}]}]},
            "Gemini": {"status": "completed", "steps": [{"type": "thought", "summary": [{"text": "hidden"}]}, {"type": "model_output", "content": [{"type": "text", "text": "answer"}]}]},
            "Anthropic": {"content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "answer"}], "stop_reason": "end_turn"},
        }
        for provider, data in fixtures.items():
            with self.subTest(provider=provider):
                request.return_value = data
                self.assertEqual(self.generate(provider), "answer")
                body = request.call_args.kwargs["json"]
                self.assertNotIn("provider", body)
                if provider == "OpenAI":
                    self.assertEqual(body["instructions"], "instruction")
                    self.assertFalse(body["store"])
                elif provider == "Gemini":
                    self.assertEqual(body["system_instruction"], "instruction")
                    self.assertEqual(body["generation_config"]["thinking_level"], "minimal")
                else:
                    self.assertEqual(body["system"], "instruction")

    @patch.object(p, "request_json")
    def test_truncation_and_empty_response_fail(self, request):
        for data in [{"content": [{"type": "text", "text": "partial"}], "stop_reason": "max_tokens"},
                     {"content": [], "stop_reason": "refusal"}]:
            request.return_value = data
            with self.assertRaises(RuntimeError):
                self.generate("Anthropic")

    @patch.object(p, "request_json")
    def test_gemini_discovery_pagination_and_filter(self, request):
        request.side_effect = [
            {"models": [{"name": "models/chat", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}], "nextPageToken": "next"},
            {"models": [{"name": "models/chat2", "supportedGenerationMethods": ["generateContent"]}]},
        ]
        self.assertEqual([r["id"] for r in p.list_models("Gemini", "key")], ["chat", "chat2"])
        self.assertEqual(request.call_args.kwargs["params"]["pageToken"], "next")

    @patch.object(p, "request_json")
    def test_anthropic_pagination_and_missing_cursor(self, request):
        request.side_effect = [{"data": [{"id": "b"}], "has_more": True, "last_id": "b"},
                               {"data": [{"id": "a"}], "has_more": False}]
        self.assertEqual([r["id"] for r in p.list_models("Anthropic", "key")], ["a", "b"])
        request.side_effect = None
        request.return_value = {"data": [], "has_more": True}
        with self.assertRaisesRegex(RuntimeError, "cursor"):
            p.list_models("Anthropic", "key")

    @patch.object(p, "request_json")
    def test_catalog_suffix_cache_and_output_filter(self, request):
        request.return_value = {"data": [{"id": "v/text:free", "architecture": {"output_modalities": ["text"]}},
                                         {"id": "v/image", "architecture": {"output_modalities": ["image"]}}]}
        self.assertEqual(p.openrouter_model("v/text:free:nitro")["id"], "v/text:free")
        self.assertEqual(p.openrouter_model("v/text:free:floor")["id"], "v/text:free")
        request.assert_called_once()

    @patch.object(p.requests, "request")
    def test_error_redacts_key_and_network_exception(self, request):
        request.return_value = Mock(status_code=401)
        request.return_value.json.return_value = {"error": {"message": "Invalid secret-key"}}
        with self.assertRaises(RuntimeError) as caught:
            p.request_json("OpenAI", "secret-key", "GET", "/models")
        self.assertNotIn("secret-key", str(caught.exception))
        request.side_effect = p.requests.Timeout("secret-key")
        with self.assertRaises(RuntimeError) as caught:
            p.request_json("OpenAI", "secret-key", "GET", "/models")
        self.assertNotIn("secret-key", str(caught.exception))

    def test_explicit_key_required_and_cache_contract(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "env-key", "GEMINI_API_KEY": "env-key",
                                      "ANTHROPIC_API_KEY": "env-key", "OPENROUTER_API_KEY": "env-key"}):
            for provider in p.BASES:
                self.assertEqual(p.resolve_key(provider, " explicit "), "explicit")
                for value in ("", "   ", None):
                    with self.subTest(provider=provider, value=value), self.assertRaisesRegex(ValueError, "Enter an API key"):
                        p.resolve_key(provider, value)
        self.assertTrue(math.isnan(node.LLMText.IS_CHANGED(cache_response=False)))
        self.assertEqual(node.LLMText.IS_CHANGED(), "cached")
        self.assertEqual(node.LLMText.RETURN_TYPES, ("STRING",))

    @patch.object(p, "request_json")
    def test_minimum_tries_next_effort_only_on_reasoning_400(self, request):
        rejected = p.APIError("Unsupported effort", 400, "reasoning.effort none is unsupported")
        request.side_effect = [rejected, {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "answer"}]}]}]
        self.assertEqual(self.generate("OpenAI", model="gpt-6"), "answer")
        self.assertEqual([call.kwargs["json"]["reasoning"]["effort"] for call in request.call_args_list], ["none", "minimal"])
        for error in [p.APIError("Rate limit", 429, "reasoning rate limit"), p.APIError("Bad input", 400, "invalid input"), RuntimeError("timeout")]:
            request.reset_mock()
            request.side_effect = error
            with self.assertRaises(RuntimeError):
                self.generate("OpenAI")
            request.assert_called_once()

    @patch.object(p, "request_json")
    def test_native_explicit_and_default_payloads(self, request):
        request.return_value = {"content": [{"type": "text", "text": "answer"}]}
        self.generate("Anthropic", reasoning="low")
        self.assertEqual(request.call_args.kwargs["json"]["thinking"], {"type": "adaptive"})
        self.assertEqual(request.call_args.kwargs["json"]["output_config"], {"effort": "low"})
        self.generate("Anthropic", reasoning="Minimum")
        self.assertEqual(request.call_args.kwargs["json"]["thinking"], {"type": "disabled"})
        self.generate("Anthropic", reasoning="Model default")
        self.assertNotIn("thinking", request.call_args.kwargs["json"])
        request.reset_mock()
        request.side_effect = p.APIError("unsupported", 400, "thinking adaptive is unsupported")
        with self.assertRaises(p.APIError):
            self.generate("Anthropic", reasoning="high")
        request.assert_called_once()

    def test_known_minimums_and_openrouter_explicit_validation(self):
        for provider, model, expected in [("OpenAI", "gpt-5", "minimal"), ("OpenAI", "o3-mini", "low"),
                                          ("Gemini", "gemini-3.8-flash", "low"), ("Gemini", "gemini-3.6-flash", "minimal")]:
            self.assertEqual(p.native_efforts(provider, model)[0], expected)
        record = {"reasoning": {"mandatory": True, "supported_efforts": ["low", "high"]}}
        self.assertEqual(p.reasoning_options(record, "high"), {"exclude": True, "effort": "high"})
        for mode in ("none", "minimal"):
            with self.assertRaises(ValueError):
                p.reasoning_options(record, mode)

    def test_schema_matches_display_order(self):
        inputs = node.LLMText.INPUT_TYPES()["required"]
        self.assertEqual(list(inputs), ["system_prompt", "text", "provider", "api_key", "model",
                                       "max_tokens", "timeout", "reasoning", "provider_slug",
                                       "routing_suffix", "cache_response"])
        self.assertTrue(inputs["text"][1]["defaultInput"])


if __name__ == "__main__":
    unittest.main()
