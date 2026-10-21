from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)
# Groq 的 llama-3.3-70b-versatile 已在 2026-08-16 停用，改用官方建議的替代模型
MODEL = "openai/gpt-oss-120b"

def quick_analysis(question, context=""):
    """快速問答"""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            reasoning_effort="low",
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
            max_tokens=2000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"分析失敗：{e}"


def explain(system, prompt, max_tokens=1500):
    """給新手看的白話說明：溫度調低，回答比較穩定；失敗時丟出例外，讓呼叫端改顯示規則版"""
    response = client.chat.completions.create(
        model=MODEL,
        reasoning_effort="low",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()
