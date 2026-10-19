import hmac

import streamlit as st

# 帳號與分組：
# - 帳號放在 Streamlit secrets：原本的 USERNAME / PASSWORD 是主帳號，家人的帳號寫在 [users.帳號] 底下
#   （password 必填，name 是顯示名稱，可省略）。
# - 每個帳號底下可以有多個分組，自選股、模擬持股都存在目前選的分組。
DEFAULT_GROUP = "我的自選股"


def accounts():
    """secrets 裡所有可以登入的帳號：{帳號: {password, name, primary}}"""
    out = {}
    for uid, info in st.secrets.get("users", {}).items():
        out[str(uid)] = {"password": str(info.get("password", "")), "name": str(info.get("name", uid)), "primary": False}
    if "USERNAME" in st.secrets:
        main = str(st.secrets["USERNAME"])
        out[main] = {"password": str(st.secrets["PASSWORD"]), "name": str(st.secrets.get("NAME", main)), "primary": True}
    return out


def login(username, password):
    """帳號密碼正確就記下登入的帳號，回傳 True"""
    acc = accounts().get(username)
    if not acc or not acc["password"] or not hmac.compare_digest(password.encode(), acc["password"].encode()):
        return False
    st.session_state.update(authenticated=True, user=username, user_name=acc["name"], primary=acc["primary"])
    st.session_state.pop("group_id", None)
    return True


def logout():
    for key in ["authenticated", "user", "user_name", "primary", "group_id"]:
        st.session_state.pop(key, None)


def render_account_sidebar():
    """側邊欄顯示目前登入的人與登出按鈕"""
    st.sidebar.caption(f"👤 {st.session_state.get('user_name', '')}")
    st.sidebar.button("登出", on_click=logout, key="logout")


def user_groups():
    """目前帳號的分組；第一次使用時建立預設分組（主帳號會先認領分組功能上線前的資料）"""
    from utils.db import get_groups, create_group, claim_unowned_groups
    owner = st.session_state.get("user", "")
    groups = get_groups(owner)
    if groups.empty and st.session_state.get("primary"):
        claim_unowned_groups(owner)
        groups = get_groups(owner)
    if groups.empty:
        create_group(owner, DEFAULT_GROUP)
        groups = get_groups(owner)
    return groups


def current_group():
    """目前選的分組 (id, 名稱)；還沒選過就用第一個分組。換頁時會記住。"""
    groups = user_groups()
    names = dict(zip(groups["id"].astype(int), groups["name"]))
    gid = st.session_state.get("group_id")
    if gid not in names:
        gid = int(groups["id"].iloc[0])
        st.session_state["group_id"] = gid
    return gid, names[gid]


def group_picker():
    """頁面上方的分組選單，回傳 (id, 名稱)"""
    gid, _ = current_group()
    groups = user_groups()
    names = dict(zip(groups["id"].astype(int), groups["name"]))
    # 選單的值由 group_id 決定（換頁、新增或刪除分組後都對得上），使用者改選時再寫回 group_id
    st.session_state["group_pick"] = gid
    st.selectbox("📁 分組", list(names), key="group_pick", format_func=names.get,
                 on_change=lambda: st.session_state.update(group_id=st.session_state["group_pick"]),
                 help="每個人的帳號底下可以有多個分組，自選股、模擬持股都存在目前選的分組；換頁時會記住。")
    return gid, names[gid]


def group_manager():
    """新增分組、改目前分組的名稱、刪除空的分組"""
    from utils.db import create_group, rename_group, delete_group, count_group_items
    owner = st.session_state.get("user", "")
    gid, name = current_group()
    groups = user_groups()
    with st.expander("✏️ 管理分組"):
        c1, c2, c3 = st.columns(3)
        with c1:
            new_name = st.text_input("改目前分組的名稱", value=name, key=f"group_rename_{gid}").strip()
            taken = new_name in set(groups["name"]) - {name}
            if st.button("儲存名稱", disabled=not new_name or new_name == name or taken):
                rename_group(gid, new_name)
                st.rerun()
            if taken:
                st.caption("已經有同名的分組")
        with c2:
            add_name = st.text_input("新增分組", placeholder="例如: 長線、短線", key="group_new").strip()
            exists = add_name in set(groups["name"])
            if st.button("➕ 新增", disabled=not add_name or exists):
                create_group(owner, add_name)
                st.session_state["group_id"] = int(user_groups().set_index("name").at[add_name, "id"])
                st.rerun()
            if exists:
                st.caption("已經有同名的分組")
        with c3:
            st.markdown(f"**刪除「{name}」**")
            n_items = count_group_items(gid)
            if len(groups) <= 1:
                st.caption("至少要留一個分組")
            elif n_items:
                st.caption(f"分組裡還有 {n_items} 筆自選股或模擬持股，清空後才能刪除")
            elif st.button("🗑️ 刪除這個分組"):
                delete_group(gid)
                st.session_state.pop("group_id", None)
                st.rerun()
