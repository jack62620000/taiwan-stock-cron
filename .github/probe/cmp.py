import sys, json, pathlib
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "app")); sys.path.insert(0, str(pathlib.Path(__file__).parent))
# real db

from streamlit.testing.v1 import AppTest
import streamlit as st
import utils.db as db
out = {}
for page in ["6_葛蘭碧八大法則.py", "7_每日選股訊號.py"]:
    st.cache_data.clear(); db.get_stock_list.cache_clear()
    at = AppTest.from_file(str(root / "app/pages" / page), default_timeout=120)
    at.session_state["authenticated"] = True; at.session_state["user"] = "tester"; at.session_state["user_name"] = "t"; at.session_state["primary"] = True
    at.run()
    assert not at.exception, at.exception
    out[page] = [d.value.to_json(force_ascii=False) for d in at.dataframe] + [m.value for m in at.metric]
    if page.startswith("7"):
        for pick in ["兩者同時", "52 週新高＋量增", "今天剛符合趨勢模板"]:
            r = [x for x in at.radio if x.key == "momentum_pick"]
            if r:
                r[0].set_value(pick).run()
                out[page + pick] = [d.value.to_json(force_ascii=False) for d in at.dataframe]
print(json.dumps(out, ensure_ascii=False))
