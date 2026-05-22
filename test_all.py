import requests, json

print("=" * 50)
print("1. 数据API")
resp = requests.get('http://localhost:5000/api/data')
d = resp.json()
stats = d['stats']
print(f'   票房天数: {stats["data_days"]}')
print(f'   最新日票房: {stats["latest_daily_box"]} 万')
print(f'   累计票房: {stats["latest_total_box"]} 万')
print(f'   豆瓣评分: {stats["douban_score"]}')
print(f'   评分人数: {stats["douban_votes"]:,}')
print(f'   影评数: {stats["total_comments"]}')
print(f'   realtime_all: {len(d.get("realtime_all",[]))} 行')

print()
print("2. 预测API")
resp = requests.get('http://localhost:5000/api/predict')
p = resp.json()
print(f'   预测天数: {len(p.get("predictions",[]))}')
for pred in p.get("predictions", [])[:3]:
    print(f'   {pred["date"]}: {pred["daily_pred"]} 万')

print()
print("3. ChatBI (DeepSeek)")
for q in ['好评占比', '票房趋势', '评分人数增长', '各评分标签分布']:
    resp = requests.post('http://localhost:5000/api/chatbi', json={'question': q, 'history': []})
    d = resp.json()
    ok = 'OK' if d['query_result']['success'] and d['query_result']['row_count'] > 0 else 'FAIL'
    print(f'   {ok}: {q} -> {d["chart_type"]} ({d["query_result"]["row_count"]} rows)')

print()
print("4. 前端页面")
resp = requests.get('http://localhost:5000/')
print(f'   状态: {resp.status_code}, 大小: {len(resp.text)} bytes')
checks = ['chart-dual', 'chart-pred', 'chatbiMsgs', 'score-card', 'box-card', 'pred-table-wrap']
all_ok = True
for c in checks:
    ok = c in resp.text
    if not ok: all_ok = False
    print(f'   {c}: {"OK" if ok else "MISSING"}')

print()
print('全部验证通过!' if all_ok else '有组件缺失!')
