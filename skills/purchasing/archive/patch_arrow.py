import os

path = r'C:\Users\GilesHugo\.openclaw\workspace\skills\purchasing\apply_proposals.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('\u2192', '->')

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
