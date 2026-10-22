import streamlit as st

PAGE = "pages/8_自選股.py"


def add_watch_form(candidates, source, date, key):
    """
    在選股頁下方放「加入自選股」：從 candidates（stock_id, stock_name）挑股票加入自選股，
    備註記下來源與日期。已經在自選股裡的股票不會重複加入，原本的備註也不會被蓋掉。
    """
    from utils.db import add_to_watchlist, get_watchlist
    from utils.groups import current_group
    gid, group_name = current_group()
    options = candidates.drop_duplicates("stock_id")
    with st.expander(f"⭐ 加入自選股（{group_name}）", expanded=False):
        if options.empty:
            st.info("上面沒有可以加入的股票")
            return
        try:
            existing = set(get_watchlist(gid)["stock_id"])
        except Exception:
            existing = set()
        labels = {r.stock_id: f"{r.stock_id} {r.stock_name}" + ("（已在自選股）" if r.stock_id in existing else "")
                  for r in options.itertuples()}
        st.caption(f"從上面列出的股票挑選加入自選股的「{group_name}」分組（要換分組請到「自選股」頁切換），"
                   "到「自選股」頁可以同時看均線買賣點與評分排行。")
        picked = st.multiselect("股票", list(labels), format_func=labels.get, key=f"watch_pick_{key}")
        if st.button("加入自選股", key=f"watch_add_{key}", type="primary", disabled=not picked):
            new = [sid for sid in picked if sid not in existing]
            for sid in new:
                add_to_watchlist(gid, sid, f"{source} {date}")
            skipped = len(picked) - len(new)
            st.success(f"已加入 {len(new)} 檔" + (f"，{skipped} 檔原本就在自選股" if skipped else ""))
        try:
            st.page_link(PAGE, label="前往自選股頁", icon="⭐")
        except Exception:  # 單獨執行這一頁（沒有從 main.py 進入）時找不到其他頁面
            st.caption("到「自選股」頁查看分析。")


def add_watch_button(stock_id, stock_name, source, date, key):
    """單一檔股票的「⭐ 加入自選股」按鈕；已經在自選股裡就顯示成不能按"""
    from utils.db import add_to_watchlist, get_watchlist
    from utils.groups import current_group
    gid, group_name = current_group()
    try:
        existing = stock_id in set(get_watchlist(gid)["stock_id"])
    except Exception:
        existing = False
    if existing:
        st.button("⭐ 已在自選股", key=f"watch_btn_{key}", disabled=True, width="stretch")
    elif st.button("⭐ 加入自選股", key=f"watch_btn_{key}", width="stretch"):
        add_to_watchlist(gid, stock_id, f"{source} {date}")
        st.success(f"已把 {stock_id} {stock_name} 加入自選股「{group_name}」")
