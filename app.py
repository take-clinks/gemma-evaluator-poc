import os
import json
import re
import traceback
from flask import Flask, render_template, request, jsonify
from groq import Groq

app = Flask(__name__, static_folder="assets", static_url_path="/assets")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


def build_prompt(headquarters_company, target_company):
    prompt = f"""
あなたは法人間取引の営業支援アナリストです。
以下の2社について評価してください。

受注側会社: {headquarters_company}
取引先候補会社: {target_company}

出力は必ず以下のJSON形式のみで返してください。
説明文やコードブロック記号は一切付けないでください。

{{
  "headquarters": "取引先候補会社の本社所在地",
  "ses": {{
    "fit": 0,
    "scale": 0,
    "continuity": 0,
    "growth": 0,
    "strategy": 0,
    "trust": 0,
    "info": 0,
    "total": 0,
    "judgement": ""
  }},
  "ai": {{
    "fit": 0,
    "scale": 0,
    "continuity": 0,
    "growth": 0,
    "strategy": 0,
    "trust": 0,
    "info": 0,
    "total": 0,
    "judgement": ""
  }}
}}

各項目（fit, scale, continuity, growth, strategy, trust, info）は0以上の整数で、
各区分（ses, ai）ごとに合計が100点になるよう配点してください。
sesはSES・システム開発の営業適合度評価、aiはAIドリブン開発の営業適合度評価です。
"""
    return prompt


def strip_code_fence(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    return text.strip()


def judge_text(score):
    if score >= 80:
        return "優先的に営業検討"
    elif score >= 60:
        return "有望"
    elif score >= 40:
        return "慎重に検討"
    else:
        return "営業優先度低め"


def recalc_block(block):
    keys = ["fit", "scale", "continuity", "growth", "strategy", "trust", "info"]
    total = 0
    for k in keys:
        try:
            total += int(block.get(k, 0))
        except (TypeError, ValueError):
            pass
    block["total"] = total
    block["judgement"] = judge_text(total)
    return block


def call_groq(headquarters_company, target_company):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = build_prompt(headquarters_company, target_company)

    completion = client.chat.completions.create(
        model="groq/compound",
        messages=[
            {"role": "user", "content": prompt},
        ],
        temperature=1,
        max_completion_tokens=2048,
        top_p=1,
        stream=False,
        stop=None,
    )

    return completion.choices[0].message.content


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/evaluate", methods=["POST"])
def evaluate():
    data = request.get_json(silent=True) or {}
    headquarters_company = (data.get("headquarters_company") or "").strip()
    target_company = (data.get("target_company") or "").strip()

    if not headquarters_company or not target_company:
        return jsonify({"error": "会社名を両方入力してください。"}), 400

    if not GROQ_API_KEY:
        return jsonify({"error": "GROQ_API_KEYが設定されていません。"}), 500

    try:
        raw_text = call_groq(headquarters_company, target_company)
    except Exception as e:
        print("=== Groq API呼び出しエラー詳細 ===")
        traceback.print_exc()
        detail = str(e)
        body_text = None
        try:
            if hasattr(e, "response") and e.response is not None:
                body_text = e.response.text
        except Exception:
            body_text = None
        if body_text:
            print("=== レスポンス本文 ===")
            print(body_text)
            detail = detail + " / " + body_text
        return jsonify({"error": "Groq API呼び出しでエラーが発生しました: " + detail}), 500

    cleaned = strip_code_fence(raw_text)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
            except json.JSONDecodeError:
                return jsonify({"error": "AIの応答をJSONとして解析できませんでした。", "raw": cleaned}), 500
        else:
            return jsonify({"error": "AIの応答をJSONとして解析できませんでした。", "raw": cleaned}), 500

    if "ses" in result:
        result["ses"] = recalc_block(result["ses"])
    if "ai" in result:
        result["ai"] = recalc_block(result["ai"])

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
