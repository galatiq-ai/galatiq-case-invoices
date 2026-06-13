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


llm = FakeLLMPlain('{"foo": "bar"}')
print('CALL RESULT:', invoke_model_with_schema(llm, ['msg'], schema=None))
