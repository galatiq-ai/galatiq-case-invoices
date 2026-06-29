import json
import os
from typing import Any, Dict

from dotenv import load_dotenv
from groq import Groq
from config import LLM_MODEL, MAX_LLM_RETRIES


load_dotenv()


class LLMTool:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

        self.client = Groq(api_key=api_key)
        self.model = LLM_MODEL
        self.max_retries = MAX_LLM_RETRIES
   
    def call_llm(self, prompt: str) -> str:
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0,
                )

                return response.choices[0].message.content.strip()

            except Exception as error:
                last_error = error
                print(f"LLM call failed. Attempt {attempt}/{self.max_retries}. Error: {error}")

        raise RuntimeError(f"LLM call failed after {self.max_retries} attempts.") from last_error

    def parse_json_response(self, raw_output: str) -> Dict[str, Any]:
        if raw_output.startswith("```json"):
            raw_output = raw_output.replace("```json", "").replace("```", "").strip()
        elif raw_output.startswith("```"):
            raw_output = raw_output.replace("```", "").strip()

        return json.loads(raw_output)