import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Assign one primary category per comment index (0..379).
# 1 QC check missed/not done
# 2 Stock status not updated
# 3 Wrong part/spec
# 4 Incomplete Asana admin
# 5 Order/invoice diligence failure
# 6 Delayed/overdue
# 7 Stock/ordering error
# 8 Missing/incorrect documentation
# 9 Communication/response gap
# 10 Other/uncategorized

cat = {}
# index: category
assign = {
 # --- Purchasing Procedure Issue ---
 0:5, 1:7, 2:10, 3:10, 4:6, 5:10, 6:10, 7:3, 8:7, 9:10,
 10:10, 11:6, 12:6, 13:5, 14:10, 15:10, 16:3, 17:10, 18:1, 19:4,
 20:6, 21:10, 22:10, 23:10, 24:5, 25:10, 26:4, 27:2, 28:10, 29:2,
 30:10, 31:2, 32:6, 33:10, 34:10, 35:5, 36:10, 37:10, 38:6, 39:10,
 40:10, 41:2, 42:4, 43:3, 44:7, 45:2, 46:10, 47:4, 48:10, 49:6,
 50:10, 51:10, 52:5, 53:2, 54:10, 55:10, 56:10, 57:10, 58:4, 59:9,
 60:10, 61:10, 62:10, 63:10, 64:10, 65:5, 66:10, 67:10, 68:10, 69:10,
 70:10, 71:5, 72:4, 73:10, 74:10, 75:10, 76:10, 77:2, 78:10, 79:10,
 80:10, 81:7, 82:2, 83:2, 84:4, 85:2, 86:6, 87:8, 88:10, 89:6,
 90:10, 91:2, 92:2, 93:2, 94:6, 95:6, 96:10, 97:10, 98:10, 99:10,
 100:7, 101:2, 102:10, 103:10, 104:4, 105:9, 106:10, 107:10, 108:6, 109:1,
 110:10, 111:10, 112:6, 113:10, 114:6, 115:8, 116:10, 117:8, 118:8, 119:1,
 120:4, 121:8, 122:10, 123:6, 124:6, 125:10, 126:2, 127:10, 128:10, 129:6,
 130:10, 131:6, 132:10, 133:6, 134:10, 135:10, 136:10, 137:2, 138:2, 139:10,
 140:6, 141:6, 142:1, 143:10, 144:2, 145:10, 146:5, 147:6, 148:6, 149:10,
 150:6, 151:10, 152:6, 153:6, 154:2, 155:5, 156:4, 157:3, 158:10, 159:10,
 160:10, 161:10, 162:10, 163:1, 164:2, 165:10, 166:2, 167:5, 168:10, 169:2,
 170:2, 171:6, 172:10, 173:7, 174:10, 175:10, 176:8, 177:7, 178:10, 179:8,
 180:1, 181:1, 182:9, 183:2, 184:2, 185:5, 186:7, 187:2, 188:1, 189:10,
 190:1, 191:10, 192:2, 193:2, 194:6, 195:10, 196:2, 197:10, 198:10, 199:6,
 200:3, 201:2, 202:10, 203:10, 204:10, 205:10, 206:6, 207:1, 208:10, 209:10,
 210:10, 211:10, 212:10, 213:10, 214:2, 215:6, 216:10, 217:8, 218:8, 219:1,
 220:10, 221:1, 222:3, 223:10, 224:3, 225:10, 226:4, 227:6, 228:10, 229:6,
 230:1, 231:2, 232:6, 233:1, 234:5, 235:5, 236:5, 237:10, 238:2, 239:10,
 240:10, 241:2, 242:10, 243:1, 244:4, 245:5, 246:10, 247:2, 248:10, 249:10,
 250:10, 251:8, 252:8, 253:4, 254:10, 255:10, 256:2, 257:2, 258:6, 259:4,
 260:6, 261:10, 262:10, 263:10, 264:10, 265:10, 266:2, 267:3, 268:10, 269:2,
 270:10, 271:4, 272:1, 273:1, 274:10, 275:4, 276:1, 277:10, 278:6, 279:7,
 280:10, 281:10, 282:6, 283:5, 284:3, 285:9, 286:2, 287:10, 288:8, 289:1,
 290:6, 291:1, 292:10, 293:3, 294:10, 295:3, 296:6, 297:2, 298:10, 299:8,
 300:10, 301:10, 302:10, 303:10, 304:10, 305:10, 306:10, 307:3, 308:10, 309:8,
 310:10, 311:1, 312:6, 313:4, 314:10, 315:3, 316:4, 317:6, 318:10, 319:1,
 320:10, 321:3, 322:8, 323:8, 324:5, 325:4, 326:6, 327:10, 328:4, 329:10,
 330:6, 331:7, 332:4, 333:9, 334:3, 335:10, 336:10, 337:10, 338:10, 339:10,
 # --- Process Issue ---
 340:10, 341:8, 342:10, 343:5, 344:10, 345:10, 346:10, 347:10, 348:8, 349:3,
 350:10, 351:9, 352:10, 353:10, 354:10, 355:10, 356:10, 357:10, 358:10, 359:10,
 360:10, 361:10, 362:6, 363:10, 364:10, 365:10, 366:10, 367:10, 368:10, 369:10,
 370:10, 371:10, 372:10, 373:9, 374:10, 375:10, 376:10, 377:10, 378:10, 379:10,
}

# Verify all 0..379 assigned
missing = [i for i in range(380) if i not in assign]
print("MISSING:", missing)

data = json.load(open('memory/richard_comments_chunk_2.json', encoding='utf-8'))
assert len(data) == 380

from collections import Counter
cc = Counter(assign.values())
print("\n=== PER-CATEGORY COUNTS ===")
names = {1:"QC check missed/not done",2:"Stock status not updated",3:"Wrong part/spec",
         4:"Incomplete Asana admin",5:"Order/invoice diligence failure",6:"Delayed/overdue",
         7:"Stock/ordering error",8:"Missing/incorrect documentation",9:"Communication/response gap",10:"Other/uncategorized"}
for k in range(1,11):
    print(f"{k:2d}. {names[k]:38s} {cc.get(k,0):3d}")

print("\n=== BY COMMENT AUTHOR ===")
auth = Counter(str(d['comment_by']) for d in data)
print("Giles:", auth.get('Giles',0))
print("Richard:", auth.get('Richard',0))
print("Others:", sum(v for k,v in auth.items() if k not in ('Giles','Richard')), "->", {k:v for k,v in auth.items() if k not in ('Giles','Richard')})
print("None(null):", auth.get('None',0))

print("\n=== DATE RANGE ===")
dates = [d['created_at'][:10] for d in data]
print("Min:", min(dates), "Max:", max(dates))

import collections
print("\n=== COUNTS BY SENTIMENT (Giles vs Richard contributions to categories) ===")
grc = Counter()
rrc = Counter()
for i,d in enumerate(data):
    c = assign[i]
    by = str(d['comment_by'])
    if by=='Giles': grc[c]+=1
    elif by=='Richard': rrc[c]+=1
for k in range(1,11):
    print(f"{k:2d} Giles:{grc.get(k,0):3d}  Richard:{rrc.get(k,0):3d}")
