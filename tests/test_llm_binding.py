import json

from src.llm_binding import invoke_model_with_schema


class FakeResponse:
    def __init__(self, content=None, parsed=None):
        self.content = content
        self.parsed = parsed


class FakeLLMPlain:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return FakeResponse(content=self._content)


class FakeLLMStructured:
    def __init__(self, parsed_obj):
        self._parsed = parsed_obj

    def invoke(self, messages, response_schema=None):
        # Simulate a client that accepts a response_schema kwarg and returns parsed
        return FakeResponse(parsed=self._parsed)


def test_fallback_json_parse():
    llm = FakeLLMPlain(json.dumps({"foo": "bar"}))
    res = invoke_model_with_schema(llm, ["msg"], schema=None)
    assert isinstance(res, dict)
    assert res["foo"] == "bar"


def test_structured_binding():
    parsed = {"decision": "approved", "reason": "ok"}
    llm = FakeLLMStructured(parsed)
    res = invoke_model_with_schema(llm, ["msg"], schema={"fields": ["decision"]})
    assert isinstance(res, dict)
    assert res["decision"] == "approved"
