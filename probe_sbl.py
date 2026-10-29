import etf_fetch as f, re
U = "https://www.sitca.org.tw/ROC/Industry/IN2211.aspx?pid=IN2222_01"
page = f.http_get(U).content.decode("utf-8", "ignore")
print("len", len(page), "title", re.findall(r"<title>(.*?)</title>", page, re.S)[:1])
for m in re.finditer(r'<(select|input)[^>]*>', page):
    t = m.group(0)
    if "ContentPlaceHolder1" in t: print(t[:250])
for sel in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', page, re.S):
    opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', sel.group(2))
    print("SELECT", sel.group(1), len(opts), opts[:12])
txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
txt = re.sub(r"<[^>]+>", " ", txt); txt = re.sub(r"\s+", " ", txt)
i = txt.find("費用"); print("TEXT", txt[max(0,i-300):i+1500])
