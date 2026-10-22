"""排程腳本共用：Supabase 查詢遇到連線中斷等暫時性錯誤就等一下重試（signals.py、check_data.py、cleanup_old_data.py 用）"""
import time


def execute(make_request, retries=4):
    """make_request 是回傳查詢物件的函式（每次重試都重新建立查詢）；最後一次仍失敗就丟出錯誤"""
    for attempt in range(1, retries + 1):
        try:
            return make_request().execute()
        except Exception as e:
            if attempt == retries:
                raise
            wait = 3 * attempt
            print(f"  連線錯誤，{wait} 秒後重試（第 {attempt} 次）：{type(e).__name__}")
            time.sleep(wait)
