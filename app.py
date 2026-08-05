import os
import json
import re
import unicodedata
import traceback
from flask import Flask, render_template, request, jsonify
import cohere
from tavily import TavilyClient

app = Flask(__name__, static_folder="assets", static_url_path="/assets")

COHERE_API_KEY = os.environ.get("COHERE_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")


def fetch_company_candidates(keyword):
    """
    入力されたキーワードから関連する企業の候補（タイトル、URL、概要）を抽出する
    """
    if not keyword:
        return []

    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    query = f'"{keyword}" 会社概要 本社所在地 公式サイト'
    
    try:
        response = tavily_client.search(
            query,
            max_results=5,
            search_depth="advanced",
            country="japan",
        )
    except Exception as e:
        print(f"Tavily Search Error: {e}")
        return []

    results = response.get("results", [])
    candidates = []

    for r in results:
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        
        # タイトルから余計なWeb装飾（| 公式サイト 等）を落とした社名ラベルを作成
        clean_name = re.sub(r'[\-|\|｜【】].*$', '', title).strip()
        
        candidates.append({
            "display_name": clean_name if clean_name else title,
            "full_title": title,
            "snippet": content[:120] + "..." if len(content) > 120 else content,
            "url": url
        })

    return candidates


def search_company_info(company_name):
    """選択された確実な社名でWeb情報を取得する"""
    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    query = f'"{company_name}" 会社概要 本社所在地 公式サイト'
    
    response = tavily_client.search(
        query,
        max_results=5,
        search_depth="advanced",
        country="japan",
    )

    results = response.get("results", [])
    if not results:
        return f"「{company_name}」の該当情報が見つかりませんでした。"

    lines = []
    for r in results:
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        lines.append(f"- {title}\n{content}\n(出典: {url})")

    return "\n".join(lines)


def build_prompt(headquarters_company, target_company, headquarters_info, target_info):
    prompt = f"""
あなたは法人間取引の営業支援アナリストです。
以下の確定された2社について、Web検索結果に基づいて評価を行ってください。

受注側会社（確定社名）: {headquarters_company}
受注側会社の検索結果:
{headquarters_info}

取引先候補会社（確定社名）: {target_company}
取引先候補会社の検索結果:
{target_info}

【評価ルール】
1. 検索結果を基に、取引先候補会社（{target_company}）の本社所在地（郵便番号含む）を「headquarters」に記載してください。
2. 検索結果から本社所在地がどうしても特定できない場合は、headquarters を "検索結果からは本社所在地を確認できませんでした" としてください。
3. 検索結果にない情報を推測や想像で補完することは固く禁止します。

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

各項目（fit, scale, continuity, growth, strategy, trust, info）は0以上の整数で、各区分（ses, ai）ごとに合計が100点になるよう配点してください。
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search_candidates", methods=["POST"])
def search_candidates():
    """ステップ1：両社の候補一覧を取得するエンドポイント"""
    data = request.get_json(silent=True) or {}
    headquarters_input = (data.get("headquarters_company") or "").strip()
    target_input = (data.get("target_company") or "").strip()

    if not headquarters_input or not target_input:
        return jsonify({"error": "両方の会社名を入力してください。"}), 400

    hq_candidates = fetch_company_candidates(headquarters_input)
    target_candidates = fetch_company_candidates(target_input)

    return jsonify({
        "headquarters_candidates": hq_candidates,
        "target_candidates": target_candidates
    })


@app.route("/evaluate", methods=["POST"])
def evaluate():
    """ステップ2：確定された社名で評価を実行するエンドポイント"""
    data = request.get_json(silent=True) or {}
    headquarters_company = (data.get("headquarters_company") or "").strip()
    target_company = (data.get("target_company") or "").strip()

    if not headquarters_company or not target_company:
        return jsonify({"error": "会社名が選択されていません。"}), 400

    debug_info = {}

    try:
        headquarters_info = search_company_info(headquarters_company)
        target_info = search_company_info(target_company)

        debug_info["headquarters_search"] = headquarters_info
        debug_info["target_search"] = target_info

        prompt = build_prompt(headquarters_company, target_company, headquarters_info, target_info)

        co = cohere.ClientV2(api_key=COHERE_API_KEY)
        response = co.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw_text = response.message.content[0].text

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "API呼び出しでエラーが発生しました: " + str(e)}), 500

    cleaned = strip_code_fence(raw_text)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            return jsonify({"error": "AIの応答をJSONとして解析できませんでした。", "raw": cleaned}), 500

    if "ses" in result:
        result["ses"] = recalc_block(result["ses"])
    if "ai" in result:
        result["ai"] = recalc_block(result["ai"])

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
