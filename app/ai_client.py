import os
import json
import requests

# This client supports two modes:
# - Gemini via a generic REST endpoint: set GEMINI_API_ENDPOINT (full URL) and GEMINI_API_KEY
# - Fallback to OpenAI if OPENAI_API_KEY is present
# The Gemini REST API shape can vary; this is a lightweight adapter that sends a JSON payload
# to the configured GEMINI_API_ENDPOINT and expects a textual answer in a common field.

class AIClient:
    def __init__(self, settings):
        self.settings = settings
        self.default_gemini_key = getattr(settings, 'GEMINI_API_KEY', None)
        self.gemini_endpoint = getattr(settings, 'GEMINI_API_ENDPOINT', None) or os.getenv('GEMINI_API_ENDPOINT')
        self.openai_key = os.getenv('OPENAI_API_KEY')
        # if openai is available, import lazily
        self._openai = None
        if not self.default_gemini_key and self.openai_key:
            try:
                import openai
                openai.api_key = self.openai_key
                self._openai = openai
            except Exception:
                self._openai = None

    def _call_gemini(self, payload: dict, api_key: str = None):
        key = api_key or self.default_gemini_key
        if not self.gemini_endpoint or not key:
            raise RuntimeError('Gemini endpoint or key not configured')
        headers = {
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
        }
        try:
            resp = requests.post(self.gemini_endpoint, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print('Gemini call error', e, getattr(e, 'response', None))
            return None

    def chat_reply(self, system_prompt: str, history: list, model: str = None, temperature: float = 0.7, api_key: str = None):
        # history is a list of (role, content)
        model = model or getattr(self.settings, 'GEMINI_MODEL_CHAT', None)
        # Prepare a simple input structure; adjust as needed for the actual Gemini API
        if (api_key or self.default_gemini_key) and self.gemini_endpoint:
            # Build messages as a single prompt
            parts = []
            if system_prompt:
                parts.append(f"System: {system_prompt}")
            for role, content in history[-(getattr(self.settings, 'DEFAULT_HISTORY_LIMIT', 20) or 20):]:
                parts.append(f"{role.capitalize()}: {content}")
            prompt_text = "\n".join(parts)
            payload = {
                'model': model,
                'input': prompt_text,
                'temperature': temperature,
                'max_output_tokens': 800,
            }
            result = self._call_gemini(payload, api_key=api_key)
            if not result:
                return None
            # Try common response shapes
            if isinstance(result, dict):
                # new PaLM-like: { 'candidates': [ { 'content': '...'} ] }
                if 'candidates' in result and isinstance(result['candidates'], list) and result['candidates']:
                    text = result['candidates'][0].get('content') or result['candidates'][0].get('text')
                    return text.strip() if text else None
                # some endpoints return output in 'output' or 'response'
                if 'output' in result and isinstance(result['output'], str):
                    return result['output'].strip()
                if 'response' in result and isinstance(result['response'], str):
                    return result['response'].strip()
                # sometimes result.choices[0].message.content
                choices = result.get('choices')
                if choices and isinstance(choices, list):
                    c0 = choices[0]
                    if isinstance(c0, dict):
                        if 'message' in c0 and isinstance(c0['message'], dict):
                            return c0['message'].get('content', '').strip()
                        if 'text' in c0:
                            return c0.get('text','').strip()
            # last resort: stringify
            return str(result)[:2000]

        # Fallback to OpenAI
        if self._openai:
            try:
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                for role, content in history[-20:]:
                    messages.append({"role": role, "content": content})
                resp = self._openai.ChatCompletion.create(
                    model=model or 'gpt-3.5-turbo',
                    messages=messages,
                    temperature=temperature,
                    max_tokens=800,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                print('OpenAI chat error', e)
                return None

        print('No AI backend configured')
        return None

    def generate_image(self, prompt: str, size: str = '1024x1024', api_key: str = None):
        # Gemini image endpoint may differ; use GEMINI_IMAGE_ENDPOINT if provided
        gemini_image_endpoint = os.getenv('GEMINI_IMAGE_ENDPOINT')
        model = getattr(self.settings, 'GEMINI_MODEL_IMAGE', None)
        key = api_key or self.default_gemini_key
        if key and gemini_image_endpoint:
            payload = {
                'model': model,
                'prompt': prompt,
                'size': size,
                'n': 1,
            }
            result = self._call_gemini(payload, api_key=api_key)
            if not result:
                return None
            # Try to find a URL or base64 image
            if isinstance(result, dict):
                data = result.get('data') or result.get('images') or result.get('outputs')
                if isinstance(data, list) and data:
                    first = data[0]
                    # url
                    url = first.get('url') or first.get('image_url')
                    if url:
                        try:
                            r = requests.get(url, timeout=30)
                            r.raise_for_status()
                            return r.content
                        except Exception as e:
                            print('Error downloading image from url', e)
                    # base64
                    b64 = first.get('b64_json') or first.get('b64') or first.get('base64')
                    if b64:
                        import base64
                        try:
                            return base64.b64decode(b64)
                        except Exception:
                            pass
                # fallback: if result has url at top-level
                if 'url' in result:
                    try:
                        r = requests.get(result['url'], timeout=30)
                        r.raise_for_status()
                        return r.content
                    except Exception as e:
                        print('Error downloading image', e)
            return None

        # Fallback to OpenAI images
        if self._openai:
            try:
                resp = self._openai.Image.create(
                    prompt=prompt,
                    n=1,
                    size=size,
                )
                url = resp['data'][0]['url']
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                return r.content
            except Exception as e:
                print('OpenAI image error', e)
                return None

        print('No image backend configured')
        return None
