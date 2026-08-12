from PIL import Image
import pytesseract
from io import BytesIO

# Accepts BytesIO or path

def ocr_image(source):
    try:
        if isinstance(source, BytesIO):
            img = Image.open(source)
        else:
            img = Image.open(source)
        text = pytesseract.image_to_string(img, lang='eng+rus')
        return text.strip()
    except Exception as e:
        print('OCR error', e)
        return ''
