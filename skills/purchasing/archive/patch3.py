import os
import re

path = r'C:\Users\GilesHugo\.openclaw\workspace\skills\purchasing\purchase_email_parser.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('load_dotenv(DOTENV_PATH)', 'load_dotenv(DOTENV_PATH, override=True)')

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
print("Added override=True")
