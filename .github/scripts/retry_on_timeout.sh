#!/usr/bin/env bash
# 用法：retry_on_timeout.sh python auto_fetch.py
# 每次最多跑 60 分鐘；超過就停掉重跑一次（資料是 upsert，重跑不會重複寫入）。
# 其他錯誤不重跑，照原本的結束代碼回報，讓排程顯示失敗。
LIMIT=${RETRY_LIMIT:-60m}
for attempt in 1 2; do
  timeout "$LIMIT" "$@"
  code=$?
  if [ "$code" -ne 124 ]; then
    exit "$code"
  fi
  echo "::warning title=執行超過 ${LIMIT}::第 ${attempt} 次執行「$*」超過 ${LIMIT}，已停止"
done
echo "::error title=執行逾時::「$*」連續兩次超過 ${LIMIT}"
exit 124
