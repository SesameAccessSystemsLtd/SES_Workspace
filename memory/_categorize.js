const fs = require('fs');
const data = JSON.parse(fs.readFileSync('C:/Users/GilesHugo/SES_Workspace/memory/richard_comments_chunk_0.json','utf8'));

// Category per comment index (0..380). Manually assigned from close reading of each comment text.
// 1 QC check missed | 2 Stock status | 3 Wrong part/spec | 4 Incomplete Asana admin
// 5 Order/invoice diligence | 6 Delayed/overdue | 7 Stock/ordering error
// 8 Missing/incorrect doc | 9 Communication/response gap | 10 Other
const cat = [
2,10,10,5,10,10,4,2,10,10,
2,9,10,10,10,10,10,3,4,6,
5,5,3,10,10,10,7,10,1,2,
9,10,10,7,10,10,9,10,10,10,
6,4,10,10,4,10,10,7,9,6,
9,4,10,8,10,10,6,4,4,10,
10,10,7,10,4,7,10,10,3,7,
2,10,10,7,4,10,10,10,10,4,
10,10,10,10,10,9,6,10,6,10,
8,10,6,10,10,10,8,10,10,2,
10,2,10,6,5,5,10,6,10,10,
6,10,10,8,10,3,4,3,3,10,
4,10,10,10,4,10,6,10,4,6,
10,6,10,10,6,6,6,3,10,6,
3,10,10,6,6,1,10,8,10,2,
10,10,10,6,6,6,5,10,2,4,
10,10,10,10,2,6,10,9,10,10,
10,10,10,2,9,10,10,8,10,10,
4,10,10,4,10,10,10,10,1,10,
10,9,10,10,7,7,9,10,6,10,
10,10,5,4,5,10,10,10,10,6,
8,10,4,5,2,2,10,10,10,10,
10,10,10,10,10,10,9,10,8,5,
2,10,10,9,10,10,10,10,8,10,
10,10,6,2,10,2,10,10,10,4,
10,10,4,10,10,1,4,10,10,10,
4,4,10,2,10,10,10,8,2,6,
6,10,3,3,3,10,6,3,4,4,
10,5,10,10,10,10,2,4,10,6,
1,3,10,10,10,10,6,10,10,10,
8,10,10,2,2,10,10,8,10,4,
10,10,10,10,10,10,10,10,10,3,
10,4,2,10,10,10,10,3,8,10,
6,8,10,7,10,5,10,10,1,10,
6,4,5,10,3,10,10,10,10,5,
7,10,10,10,6,10,10,10,10,10,
10,10,6,4,10,4,8,8,10,8,
4,10,10,10,10,10,10,10,10,10,
10
];

if (cat.length !== data.length) {
  console.error('MISMATCH length: cat', cat.length, 'vs data', data.length);
  process.exit(1);
}
console.log('Total comments:', data.length, 'OK');

const names = {
  1:'QC check missed/not done',2:'Stock status not updated',3:'Wrong part/spec',
  4:'Incomplete Asana admin',5:'Order/invoice diligence failure',6:'Delayed/overdue',
  7:'Stock/ordering error',8:'Missing/incorrect documentation',9:'Communication/response gap',10:'Other/uncategorized'
};
const counts = {1:0,2:0,3:0,4:0,5:0,6:0,7:0,8:0,9:0,10:0};
data.forEach((c,i)=>{ counts[cat[i]]++; });
console.log('\n=== PER-CATEGORY COUNTS ===');
for (let k=1;k<=10;k++) console.log(`${k}. ${names[k]}: ${counts[k]}`);
console.log('Total categorized:', Object.values(counts).reduce((a,b)=>a+b,0));

const by={};
data.forEach(c=>{const a=(c.comment_by==null?'(null/system)':c.comment_by);by[a]=(by[a]||0)+1;});
console.log('\n=== AUTHOR COUNTS ===');
Object.entries(by).sort((a,b)=>b[1]-a[1]).forEach(([a,n])=>console.log(`${a}: ${n}`));

const dates = data.map(c=>c.created_at).filter(Boolean).sort();
console.log('\n=== DATE RANGE ===');
console.log('Earliest:', dates[0]);
console.log('Latest  :', dates[dates.length-1]);

console.log('\n=== ISSUE CATEGORIES (1-9) by author ===');
const g={},r={},o={};
data.forEach((c,i)=>{
  if(cat[i]>=10) return;
  const b = c.comment_by==='Giles'?g : c.comment_by==='Richard'?r : o;
  b[cat[i]]=(b[cat[i]]||0)+1;
});
console.log('Giles :', JSON.stringify(g));
console.log('Richard:', JSON.stringify(r));
console.log('Other :', JSON.stringify(o));
