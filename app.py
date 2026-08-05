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


def normalize_str(text):
    """比較用の文字正規化（全角半角・大文字小文字の統一）"""
    if not text:
        return ""
    return unicodedata.normalize('NFKC', text).lower()


def fetch_company_candidates(keyword):
    """
    キーワードに対し、社名テキスト自体にキーワードが含まれる企業（部分一致）および同名異住所の企業のみを厳格抽出する
    """
    if not keyword:
        return []

    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    
    # 部分一致検索（アルファシステム、アルファシステムズ等も含めて検索）
    query = f'{keyword} 企業 会社概要 本社所在地 公式サイト'
    
    try:
        response = tavily_client.search(
            query,
            max_results=15,
            search_depth="advanced",
            country="japan",
        )
        results = response.get("results", [])
        if not results:
            return []

        search_text_lines = []
        for r in results:
            search_text_lines.append(f"Title: {r.get('title')}\nContent: {r.get('content')}")
        search_text = "\n---\n".join(search_text_lines)

        # AIプロンプト：社名テキストにキーワードが含まれない別名子会社・グループ会社を【厳禁】とする
        parse_prompt = f"""
以下のWeb検索結果から、キーワード「{keyword}」が社名に含まれる実在企業のみを抽出してリスト化してください。

検索結果:
{search_text}

【厳格な抽出ルール（違反厳禁）】
1. 抽出対象は、社名テキスト自体に「{keyword}」（またはその読み）が含まれている企業【のみ】です。
   （例: キーワードが「アルファ」の場合 ➔ 「アルファ」「アルファシステム」「アルファシステムズ」等は可）
2. グループ会社、親会社、子会社であっても、社名テキストに「{keyword}」が含まれていない企業（例: 別名の関連会社など）は【絶対にリストに含めないでください】。
3. 社名テキストが全く同じであっても、本社所在地（都道府県・市区町村）が異なる場合は【別の企業】としてそれぞれ抽出してください。
4. 各企業について、正確な正式社名、本社所在地、および簡潔な特徴を明記してください。

出力は必ず以下のJSON配列形式のみで返してください。説明文やコードブロック記号は一切付けないでください。

[
  {{
    "company_name": "正確な正式社名",
    "location": "本社所在地（例: 東京都千代田区）",
    "description": "事業内容や市場（例: システム開発 / 未上場）"
  }}
]
"""
        co = cohere.ClientV2(api_key=COHERE_API_KEY)
        parse_res = co.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": parse_prompt}],
            temperature=0.1,
        )
        
        raw_json = strip_code_fence(parse_res.message.content[0].text)
        candidates_data = json.loads(raw_json)

        norm_keyword = normalize_str(keyword)
        formatted_candidates = []

        for item in candidates_data:
            c_name = item.get("company_name", "").strip()
            loc = item.get("location", "").strip()
            desc = item.get("description", "").strip()
            
            if not c_name:
                continue

            norm_c_name = normalize_str(c_name)

            # 【Python側の安全網フィルター】
            # AIが事故で「LainZ」などの社名テキストにキーワードが含まれない会社を出してきたら強制排除する
            if norm_keyword not in norm_c_name:
                continue

            info_str = f"本社: {loc}" if loc else "本社所在地不明"
            if desc:
                info_str += f" / {desc}"

            formatted_candidates.append({
                "display_name": c_name,
                "full_title": c_name,
                "snippet": info_str
            })

        # 万が一フィルターで0件になった場合の保険
        if not formatted_candidates:
            formatted_candidates.append({
                "display_name": keyword,
                "full_title": keyword,
                "snippet": "直接入力名を使用"
            })

        return formatted_candidates

    except Exception as e:
        print(f"Candidate Extraction Error: {e}")
        traceback.print_exc()
        return [{"display_name": keyword, "full_title": keyword, "snippet": "直接入力名を使用"}]


def search_company_info(company_name):
    """確定社名で評価用Web情報を取得"""
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
        return f"「{company_name}」に関する明確な情報が見つかりませんでした。"

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

受注側会社: {headquarters_company}
受注側会社の検索結果:
{headquarters_info}

取引先候補会社: {target_company}
取引先候補会社の検索結果:
{target_info}

【評価ルール】
1. 検索結果を基に、取引先候補会社（{target_company}）の本社所在地（郵便番号含む）を「headquarters」に記載してください。
2. 検索結果から本社所在地が特定できない場合は、headquarters を "検索結果からは本社所在地を確認できませんでした" としてください。
3. 検索結果に存在しない情報を推測・創作することは厳禁です。

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
    data = request.get_json(silent=True) or {}
    headquarters_input = (data.get("headquarters_company") or "").strip()
    target_input = (data.get("target_company") or "").strip()

    if not headquarters_input or not target_input:
        return jsonify({"error": "会社名を両方入力してください。"}), 400

    hq_candidates = fetch_company_candidates(headquarters_input)
    target_candidates = fetch_company_candidates(target_input)

    return jsonify({
        "headquarters_candidates": hq_candidates,
        "target_candidates": target_candidates
    })


@app.route("/evaluate", methods=["POST"])
def evaluate():
    data = request.get_json(silent=True) or {}
    headquarters_company = (data.get("headquarters_company") or "").strip()
    target_company = (data.get("target_company") or "").strip()
    debug_mode = bool(data.get("debug"))

    if not headquarters_company or not target_company:
        return jsonify({"error": "会社名を選択または指定してください。"}), 400

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

    if debug_mode:
        result["debug"] = debug_info

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
