from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

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
