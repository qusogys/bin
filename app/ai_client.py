import os
import openai

class AIClient:
    def __init__(self, api_key: str):
        openai.api_key = api_key

    def chat_reply(self, system_prompt: str, history: list, temperature: float = 0.7):
        # history: list of (role, content)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for role, content in history[-20:]:
            messages.append({"role": role, "content": content})
        try:
            resp = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=temperature,
                max_tokens=800,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print('AI chat error', e)
            return None

    def generate_image(self, prompt: str, size: str = "1024x1024"):
        try:
            resp = openai.Image.create(
                prompt=prompt,
                n=1,
                size=size
            )
            url = resp['data'][0]['url']
            # download image
            import requests
            r = requests.get(url)
            return r.content
        except Exception as e:
            print('Image generation error', e)
            return None
