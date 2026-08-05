import os
import json
import re
import traceback
from flask import Flask, render_template, request, jsonify
import cohere
from tavily import TavilyClient

app = Flask(__name__, static_folder="assets", static_url_path="/assets")

COHERE_API_KEY = os.environ.get("COHERE_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")


def search_company_info(company_name):
    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    
    # 【改善1】「アルファシステム」検索時に「アルファシステムズ」を拾うのを防ぐ動的除外設定
    exclude_term = ""
    if not company_name.endswith("ズ"):
        # 末尾に「ズ」がない社名の場合、「社名+ズ」を検索から除外する
        exclude_term = f' -"{company_name}ズ"'
    
    query = f'日本 企業 "{company_name}"{exclude_term} 会社概要 本社所在地 公式サイト'
    
    response = tavily_client.search(
        query,
        max_results=5,
        search_depth="advanced",
        country="japan",
    )

    results = response.get("results", [])
    if not results:
        return "検索結果なし（該当する会社情報が見つかりませんでした）"

    lines = []
    for r in results:
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        lines.append(f"- {title}\n{content}\n(出典: {url})")

    return "\n".join(lines)


def build_prompt(headquarters_company, target_company, headquarters_info, target_info):
    # 【改善2】AIに対する「一文字違いの別会社」の混同防止プロンプトを強化
    prompt = f"""
あなたは法人間取引の営業支援アナリストです。
以下の2社について、下記のWeb検索結果を根拠として評価してください。

受注側会社: {headquarters_company}
受注側会社に関するWeb検索結果:
{headquarters_info}

取引先候補会社: {target_company}
取引先候補会社に関するWeb検索結果:
{target_info}

【極めて重要な制約】
1. 表記揺れ（「株式会社」の有無、全角半角、前株/後株など）は同一視して構いません。
2. ただし、「〜システム」と「〜システムズ」、「〜テクノロジー」と「〜テクノロジーズ」のように、語尾の「ズ」や一文字の違いで「全く別の会社」が存在します。これらを決して同一会社とみなさないでください。
3. 検索結果が指定された会社名（{target_company}）と異なる別会社のものである場合は、絶対にその情報を使わないでください。
4. Web検索結果から指定された会社の本社所在地が確認できない場合は、headquarters の値を "検索結果からは本社所在地を確認できませんでした" としてください。
5. 検索結果にない情報を想像で補完・創作することは厳禁です。

出力は必ず以下のJSON形式のみで返してください。
説明文やコードブロック記号は一切付けないでください。

{{
  "headquarters": "取引先候補会社の正確な本社所在地（郵便番号含む）、確認できない場合は確認できない旨",
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
取引先候補会社の実在性が全く確認できない場合（または別会社の情報しかない場合）は、すべての項目を0点にしてください。
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


def call_cohere(headquarters_company, target_company, debug_info):
    headquarters_info = search_company_info(headquarters_company)
    target_info = search_company_info(target_company)

    debug_info["headquarters_search"] = headquarters_info
    debug_info["target_search"] = target_info

    prompt = build_prompt(headquarters_company, target_company, headquarters_info, target_info)

    co = cohere.ClientV2(api_key=COHERE_API_KEY)
    response = co.chat(
        model="command-a-03-2025",
        messages=[
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,  # 【改善3】ランダム性を下げて命令（制約）を厳格に守らせる(0.3 -> 0.1)
    )

    return response.message.content[0].text


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/evaluate", methods=["POST"])
def evaluate():
    data = request.get_json(silent=True) or {}
    headquarters_company = (data.get("headquarters_company") or "").strip()
    target_company = (data.get("target_company") or "").strip()
    debug_mode = bool(data.get("debug"))

    if not headquarters_company or not target_company:
        return jsonify({"error": "会社名を両方入力してください。"}), 400

    if not COHERE_API_KEY:
        return jsonify({"error": "COHERE_API_KEYが設定されていません。"}), 500

    if not TAVILY_API_KEY:
        return jsonify({"error": "TAVILY_API_KEYが設定されていません。"}), 500

    debug_info = {}

    try:
        raw_text = call_cohere(headquarters_company, target_company, debug_info)
    except Exception as e:
        print("=== API呼び出しエラー詳細 ===")
        traceback.print_exc()
        return jsonify({"error": "API呼び出しでエラーが発生しました: " + str(e)}), 500

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

    if debug_mode:
        result["debug"] = debug_info

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
