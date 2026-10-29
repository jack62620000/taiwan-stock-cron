import etf_fetch as f, re
U = f.SITCA_TOP10_URL
page = f.http_get(U).content.decode("utf-8","ignore")
for m in re.finditer(r'<(select|input)[^>]*>', page):
    t = m.group(0)
    if 'ContentPlaceHolder1' in t and '__' not in t[:20]:
        print(t[:300])
sel = re.search(r'<select[^>]*ddlQ_YM[^>]*>(.*?)</select>', page, re.S)
print("YM selected:", re.findall(r'<option selected="selected" value="([^"]*)"', sel.group(1)) if sel else None)
def hidden(p): return dict(re.findall(r'<input[^>]+name="(__[A-Z]+)"[^>]+value="([^"]*)"', p))
def funds(h):
    rows,unk = f.parse_sitca_top10(h,"x",{})
    names = re.findall(r'<td[^>]*rowspan[^>]*>(.*?)</td>', h, re.S)
    return len(unk), sorted(unk)[:3]
base = {"ctl00$ContentPlaceHolder1$ddlQ_YM":"202608","ctl00$ContentPlaceHolder1$rdo1":"rbClass","ctl00$ContentPlaceHolder1$ddlQ_Class":"AH11"}
# A: direct query (current code)
form = dict(hidden(page)); form.update(base); form["ctl00$ContentPlaceHolder1$BtnQuery"]="查詢"
print("A direct:", funds(f.http_post(U, data=form).content.decode("utf-8","ignore")))
# B: postback month, then postback radio, then query
form = dict(hidden(page)); form.update(base); form["__EVENTTARGET"]="ctl00$ContentPlaceHolder1$ddlQ_YM"; form["__EVENTARGUMENT"]=""
p2 = f.http_post(U, data=form).content.decode("utf-8","ignore")
form = dict(hidden(p2)); form.update(base); form["__EVENTTARGET"]="ctl00$ContentPlaceHolder1$rbClass"; form["__EVENTARGUMENT"]=""
p3 = f.http_post(U, data=form).content.decode("utf-8","ignore")
sel = re.search(r'<select[^>]*ddlQ_Class[^>]*>(.*?)</select>', p3, re.S)
print("class opts after:", re.findall(r'value="([^"]*)"', sel.group(1))[:30] if sel else None)
for cls in ["AH11","AH21"]:
    form = dict(hidden(p3)); form.update(base); form["ctl00$ContentPlaceHolder1$ddlQ_Class"]=cls; form["ctl00$ContentPlaceHolder1$BtnQuery"]="查詢"
    h = f.http_post(U, data=form).content.decode("utf-8","ignore")
    print("B", cls, funds(h), re.findall(r'(\d{4}年\d{1,2}月|\d{6})', h)[:3])
# C: only month postback then query
form = dict(hidden(p2)); form.update(base); form["ctl00$ContentPlaceHolder1$BtnQuery"]="查詢"
print("C:", funds(f.http_post(U, data=form).content.decode("utf-8","ignore")))
