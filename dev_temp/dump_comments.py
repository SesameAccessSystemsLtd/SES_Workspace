import json, re, html

data = json.load(open('memory/richard_comments_chunk_1.json', encoding='utf-8'))
out = open('dev_temp/all_comments.txt', 'w', encoding='utf-8')
for i, d in enumerate(data):
    txt = d['text']
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = html.unescape(txt)
    txt = ' '.join(txt.split())
    out.write(f'===[{i}] {d["project"]} | by={d["comment_by"]} | {d["created_at"]}\n')
    out.write(f'TASK: {d["task_name"][:90]}\n')
    out.write(f'TEXT: {txt}\n')
    out.write('\n')
out.close()
print('done', len(data))
