import json
import logging
import time
from typing import Type, TypeVar, Optional, Dict, Any
import httpx
from pydantic import BaseModel, ValidationError
from backend.config import settings

logger = logging.getLogger("llm_adapter")

T = TypeVar("T", bound=BaseModel)

class LLMRateLimiter:
    """Simple sliding-window rate limiter for LLM requests (30 requests/minute)."""
    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps: list[float] = []

    def check_and_record(self):
        now = time.time()
        self.timestamps = [t for t in self.timestamps if now - t < self.window_seconds]
        if len(self.timestamps) >= self.max_requests:
            raise Exception("Rate limit exceeded for LLM generation. Please wait a few seconds and try again.")
        self.timestamps.append(now)

rate_limiter = LLMRateLimiter()

class LLMAdapter:
    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.api_key = (settings.LLM_API_KEY or "").strip()
        self.model = settings.LLM_MODEL or "gemini-3.5-flash"

    async def _call_gemini(self, prompt: str, system_instruction: str = "") -> str:
        """Calls Google Gemini Generative Language REST API with fallback models."""
        candidate_models = list(dict.fromkeys([self.model, "gemini-3.5-flash", "gemini-3.8-flash", "gemini-flash-latest"]))
        
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key
        }
        
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2
            }
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        last_resp = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model_name in candidate_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                resp = await client.post(url, json=payload, headers=headers)
                last_resp = resp
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "")
                elif resp.status_code not in [404, 503]:
                    # For auth or bad request errors (400, 401, 403), stop retrying
                    break

            if last_resp is not None and last_resp.status_code != 200:
                raise Exception(f"Gemini API returned status {last_resp.status_code}: {last_resp.text}")
            raise Exception("Gemini API candidate has no parts")

    async def _call_openai(self, prompt: str, system_instruction: str = "") -> str:
        """Calls OpenAI Chat Completion REST API."""
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model if "gpt" in self.model else "gpt-4o-mini",
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise Exception(f"OpenAI API returned status {resp.status_code}: {resp.text}")
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def generate_raw(self, prompt: str, system_instruction: str = "") -> str:
        """Executes LLM call with rate-limiting based on configured provider."""
        rate_limiter.check_and_record()
        if self.provider == "openai":
            return await self._call_openai(prompt, system_instruction)
        else:
            return await self._call_gemini(prompt, system_instruction)

    async def generate_structured(
        self,
        prompt: str,
        schema_class: Type[T],
        system_instruction: str = "",
        max_retries: int = 1
    ) -> T:
        """
        Requests structured JSON output, validates with Pydantic schema,
        and retries once if JSON is invalid or fails schema validation.
        """
        # Append schema description to prompt/system instruction
        schema_json = json.dumps(schema_class.model_json_schema(), indent=2)
        full_system = f"{system_instruction}\n\nYou MUST return valid JSON adhering strictly to this JSON Schema:\n{schema_json}"
        
        last_error = None
        current_prompt = prompt

        for attempt in range(max_retries + 1):
            try:
                raw_text = await self.generate_raw(current_prompt, full_system)
                
                # Clean any markdown code fences if returned
                cleaned = raw_text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()

                parsed_json = json.loads(cleaned)
                validated = schema_class.model_validate(parsed_json)
                return validated
            except (json.JSONDecodeError, ValidationError, Exception) as e:
                last_error = e
                logger.warning(f"LLM generation attempt {attempt + 1} failed validation: {e}")
                if attempt < max_retries:
                    # Provide feedback on retry
                    current_prompt = f"{prompt}\n\nNOTE: Your previous response was invalid with error: {str(e)}. Please correct this and return ONLY valid JSON matching the schema."
                    time.sleep(0.5)

        raise Exception(f"Failed to generate valid structured output after {max_retries + 1} attempts. Error: {last_error}")

llm_adapter = LLMAdapter()
