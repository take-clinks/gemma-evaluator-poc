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


def clean_company_name(name):
    """法人格やスペースを除去して純粋な社名テキストを取り出す汎用関数"""
    return re.sub(r'株式会社|有限会社|合同会社|一般社団法人|（株）|\(株\)|\s+', '', name)


def search_company_info(company_name):
    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    
    # 完全一致検索を強制
    query = f'日本 企業 "{company_name}" 会社概要 本社所在地 公式サイト'
    
    response = tavily_client.search(
        query,
        max_results=8,
        search_depth="advanced",
        country="japan",
    )

    results = response.get("results", [])
    if not results:
        return "検索結果なし（該当する会社情報が見つかりませんでした）"

    clean_target = clean_company_name(company_name)
    
    # 汎用ロジック: 指定社名の直後にカタカナが続いて別名詞化しているか判定するパターン
    # 例: clean_target="アルファシステム" の場合、直後にカタカナ(ズ等)がつく "アルファシステムズ" を検出
    extension_pattern = re.escape(clean_target) + r'[\u30A0-\u30FF]'

    lines = []
    for r in results:
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        
        clean_title = clean_company_name(title)
        
        # 検索結果のタイトルが「アルファシステムズ」のように語尾拡張されており、
        # かつ入力された社名自体（clean_target）にはその拡張が含まれていない場合は別会社とみなして除外
        if re.search(extension_pattern, clean_title) and not re.search(extension_pattern, clean_target):
            continue

        lines.append(f"- {title}\n{content}\n(出典: {url})")

    if not lines:
        return f"「{company_name}」に関する明確な情報が見つかりませんでした。（類似の別会社情報は除外されました）"

    return "\n".join(lines)


def build_prompt(headquarters_company, target_company, headquarters_info, target_info):
    prompt = f"""
あなたは法人間取引の営業支援アナリストです。
以下の2社について、下記のWeb検索結果を根拠として評価してください。

受注側会社: {headquarters_company}
受注側会社に関するWeb検索結果:
{headquarters_info}

取引先候補会社: {target_company}
取引先候補会社に関するWeb検索結果:
{target_info}

【厳格な判定ルール】
1. 「株式会社」の有無や全角半角の違い、前株/後株の違いは同一会社として扱ってください。
2. ただし、「〜システム」と「〜システムズ」、「〜ジャパン」の有無など、社名テキスト自体が異なる場合は「絶対に別の会社」として扱ってください。
3. Web検索結果が指定された会社（{target_company}）と異なる別会社のものである場合、または情報が見つからない場合は、headquarters の値を必ず "検索結果からは本社所在地を確認できませんでした" としてください。
4. 情報が不十分・確認できない場合は、ses および ai の全評価項目（fit, scale, continuity, growth, strategy, trust, info）をすべて 0 点にしてください。
5. 検索結果に存在しない住所・郵便番号・ビル名を自分で推測・創作することは厳禁です。

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

各項目は0以上の整数で、各区分（ses, ai）ごとに合計が100点になるよう配点してください（情報不足時は全項目0点）。
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
        temperature=0.0,
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
