import json, collections
c = json.load(open("cases.json", encoding="utf-8")); o = json.load(open("out_js.json", encoding="utf-8"))
print(collections.Counter((x["fn"], y.get("err")) for x, y in zip(c, o) if "err" in y))
