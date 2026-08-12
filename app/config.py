import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    # Gemini settings
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_API_ENDPOINT = os.getenv('GEMINI_API_ENDPOINT')
    GEMINI_MODEL_CHAT = os.getenv('GEMINI_MODEL_CHAT', 'gemini')
    GEMINI_MODEL_IMAGE = os.getenv('GEMINI_MODEL_IMAGE', 'vision')

    BOT_DEFAULT_PROMPT = os.getenv('BOT_DEFAULT_PROMPT', 'You are a helpful assistant.')
    ALLOWED_USER_IDS = [int(x) for x in os.getenv('ALLOWED_USER_IDS','').split(',') if x.strip().isdigit()]
    DB_PATH = os.getenv('DB_PATH', 'data/bot.db')
    OCR_ENABLED = os.getenv('OCR_ENABLED','true').lower() in ('1','true','yes')
    IMAGE_PROVIDER = os.getenv('IMAGE_PROVIDER','gemini')
    IMAGE_DEFAULT_SIZE = os.getenv('IMAGE_DEFAULT_SIZE','1024x1024')
    DEFAULT_TEMPERATURE = float(os.getenv('DEFAULT_TEMPERATURE','0.7'))
    MASTER_KEY = os.getenv('MASTER_KEY')

settings = Settings()
