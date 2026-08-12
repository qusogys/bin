## Gemini integration and webhook

I implemented a generic Gemini adapter and added webhook support.

To use Gemini via a REST endpoint provide the following ENV vars:
- GEMINI_API_KEY — bearer token for Gemini
- GEMINI_API_ENDPOINT — full URL to the model prediction endpoint (e.g. https://us-central1-aiplatform.googleapis.com/v1/projects/...) if using PaLM/Vertex AI
- GEMINI_MODEL_CHAT — model name for chat (optional)
- GEMINI_MODEL_IMAGE — model name for images (optional)

Webhook support:
- Set USE_WEBHOOK=1, WEBHOOK_URL (public URL), and optionally WEBHOOK_PATH (default /webhook), PORT and HOST.

Notes:
- The Gemini adapter is intentionally generic: different Gemini/PaLM endpoints differ in request/response shape. Provide GEMINI_API_ENDPOINT that accepts the minimal payload used by the adapter ("model", "input", "temperature"). You can tweak ai_client._call_gemini to match the exact API response structure of your endpoint.
- If GEMINI_* envs are not provided, the client will fall back to OpenAI if OPENAI_API_KEY is set.

