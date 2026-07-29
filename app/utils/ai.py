from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

def analyze_stock(stock_info, price_data, ins_data, margin_data, 
                  holder_data, revenue_data, quarterly_data, 
                  futures_data, signals):
    """全面性個股 AI 分析"""

    # 整理各面向資料
    latest_price = price_data.iloc[-1] if not price_data.empty else {}
    latest_ins   = ins_data.iloc[-1]   if not ins_data.empty   else {}
    latest_margin = margin_data.iloc[-1] if not margin_data.empty else {}
    latest_holder = holder_data.iloc[-1] if not holder_data.empty else {}
    latest_revenue = revenue_data.iloc[-1] if not revenue_data.empty else {}
    latest_quarterly = quarterly_data.iloc[-1] if not quarterly_data.empty else {}

    # 計算近期外資累計買賣超（10天）
    foreign_10d = ins_data["foreign_net_shares"].tail(10).sum() if not ins_data.empty else 0
    sitca_10d   = ins_data["sitca_net_shares"].tail(10).sum()   if not ins_data.empty else 0

    # 計算月營收年增率趨勢
    yoy_trend = revenue_data["yoy_growth"].tail(3).tolist() if not revenue_data.empty else []

    prompt = f"""
你是一位專業的台股分析師，請根據以下數據對 {stock_info.get('stock_name', '')}（{stock_info.get('stock_id', '')}）進行全面性分析。
請用繁體中文回答，分為以下六個面向，每個面向給出重點觀察和結論。

## 股票基本資訊
- 股票代號：{stock_info.get('stock_id', 'N/A')}
- 公司名稱：{stock_info.get('stock_name', 'N/A')}
- 產業別：{stock_info.get('industry', 'N/A')}
- 市場：{stock_info.get('market', 'N/A')}

## 一、價值面
- 最新收盤價：{latest_price.get('close_price', 'N/A')}
- 每股淨值：{latest_quarterly.get('book_value', 'N/A')}
- 股價淨值比（P/B）：{latest_quarterly.get('pb_ratio', 'N/A')} 倍
- EPS（最新季）：{latest_quarterly.get('eps', 'N/A')} 元
- ROE：{latest_quarterly.get('roe', 'N/A')}%

## 二、成長面
- 最新月營收年增率：{latest_revenue.get('yoy_growth', 'N/A')}%
- 最新月營收月增率：{latest_revenue.get('mom_growth', 'N/A')}%
- 近3期年增率趨勢：{yoy_trend}
- 本期淨利：{latest_quarterly.get('net_income', 'N/A')} 千元

## 三、籌碼面
- 外資近10日買賣超：{foreign_10d:,} 股
- 投信近10日買賣超：{sitca_10d:,} 股
- 外資今日買賣超：{latest_ins.get('foreign_net_shares', 'N/A')} 股
- 投信今日買賣超：{latest_ins.get('sitca_net_shares', 'N/A')} 股
- 自營商今日買賣超：{latest_ins.get('dealers_net_shares', 'N/A')} 股
- 大戶持股比例：{latest_holder.get('big_holder_ratio', 'N/A')}%

## 四、資金面
- 融資餘額：{latest_margin.get('margin_balance', 'N/A')} 股
- 融券餘額：{latest_margin.get('short_balance', 'N/A')} 股

## 五、技術面
- RSI：{signals.get('RSI', {}).get('value', 'N/A')} （{signals.get('RSI', {}).get('status', 'N/A')}）
- KD：K={signals.get('KD', {}).get('K', 'N/A')}  D={signals.get('KD', {}).get('D', 'N/A')} （{signals.get('KD', {}).get('status', 'N/A')}）
- MACD：{signals.get('MACD', {}).get('value', 'N/A')}  Signal={signals.get('MACD', {}).get('signal', 'N/A')}
- 布林通道位置：{signals.get('Bollinger', {}).get('position', 'N/A')}
- 股價 vs MA20：{'上方' if signals.get('MA20', {}).get('above') else '下方'}
- 股價 vs MA60：{'上方' if signals.get('MA60', {}).get('above') else '下方'}

## 六、綜合結論
請根據以上五個面向給出：
1. 各面向評分（1-5分）
2. 整體綜合評分
3. 主要優勢與風險
4. 短中期操作建議

請注意：以上分析僅供參考，不構成投資建議。
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "你是一位專業的台股分析師，擅長從多個面向分析個股。請用繁體中文提供詳細且專業的分析。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.7,
            max_tokens=2000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI 分析失敗：{e}"


def quick_analysis(question, context=""):
    """快速問答"""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "你是一位專業的台股分析師，請用繁體中文回答問題。"
                },
                {
                    "role": "user",
                    "content": f"{context}\n\n問題：{question}" if context else question
                }
            ],
            temperature=0.7,
            max_tokens=1000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"分析失敗：{e}"