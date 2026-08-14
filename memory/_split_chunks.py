import json
data = json.load(open('memory/richard_proc_issue_comments.json'))
# Split into 3 chunks for parallel subagent categorization
chunks = [data[i::3] for i in range(3)]
for i, c in enumerate(chunks):
    json.dump(c, open(f'memory/richard_comments_chunk_{i}.json', 'w'))
    dates = [d['created_at'][:10] for d in c if d['created_at']]
    print(f'Chunk {i}: {len(c)} comments, {min(dates)} to {max(dates)}')
