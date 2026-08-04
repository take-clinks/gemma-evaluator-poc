import os
import json
import re

from flask import Flask, request, render_template_string
from google import genai
from google.genai import types

app = Flask(__name__)

api_key = os.environ.get("GEMINI_API_KEY")

gemini_client = None
if api_key:
    gemini_client = genai.Client(api_key=api_key)

MODEL = "gemma-4-26b-a4b-it"

PROMPT = """法人間取引の営業評価AIとして、下記2社を公開情報のみで評価し、
JSON構造のみを出力してください。説明文・前置き・コードブロック記号は禁止。
値がすべて日本語。推測で補完しない。

受注側：{a}
取引先：{b}

配点（各区分100点満点、SES区分とAI区分は独立evaluate）：
fit25 scale20 continuity15 growth15 strategy10 trust10 info5

出力JSON：
{{
  "headquarters": "取引先本社所在地。不明なら確認できません",
  "ses": {{"fit":0,"scale":0,"continuity":0,"growth":0,"strategy":0,"trust":0,"info":0,"total":0,"judgement":""}},
  "ai": {{"fit":0,"scale":0,"continuity":0,"growth":0,"strategy":0,"trust":0,"info":0,"total":0,"judgement":""}}
}}"""

HTML = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Gemma検証用アプリ</title>
</head>
<body style="font-family: sans-serif; max-width: 800px; margin: 40px auto;">
  <h1>Gemma 4 26B 検証用アプリ（PoC）</h1>

  <form method="POST" action="/">
    <label>受注側会社名</label><br>
    <input type="text" name="company_a" value="{{a}}" style="width:100%; padding:8px;"><br><br>

    <label>取引先会社名</label><br>
    <input type="text" name="company_b" value="{{b}}" style="width:100%; padding:8px;"><br><br>

    <button type="submit" style="padding:10px 20px;">検証実行</button>
  </form>

  {% if raw_text %}
    <h2>Gemmaからの生の応答</h2>
    <pre style="background:#f1f1f1; padding:10px; white-space:pre-wrap;">{{raw_text}}</pre>
  {% endif %}

  {% if parsed_json %}
    <h2>JSON解析結果</h2>
    <pre style="background:#e8f5e9; padding:10px; white-space:pre-wrap;">{{parsed_json}}</pre>
  {% endif %}

  {% if error %}
    <h2 style="color:red;">エラー</h2>
    <pre style="background:#ffebee; padding:10px; white-space:pre-wrap;">{{error}}</pre>
  {% endif %}
</body>
</html>"""


def strip_code_fence(text):
    stripped = text.strip()
    stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
    stripped = re.sub(r"```\s*$", "", stripped)
    return stripped.strip()


@app.route("/", methods=["GET", "POST"])
def index():
    a = ""
    b = ""
    raw_text = None
    parsed_json = None
    error = None

    if request.method == "POST":
        a = request.form.get("company_a", "").strip()
        b = request.form.get("company_b", "").strip()

        if not a or not b:
            error = "両方の会社名を入力してください。"
        elif gemini_client is None:
            error = "GEMINI_API_KEYが設定されていません。"
        else:
            try:
                prompt_text = PROMPT.format(a=a, b=b)

                contents = [
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt_text)],
                    ),
                ]

                tools = []  （Groundingを使わない）

                config = types.GenerateContentConfig(
                    tools=tools,
                )

                full_text = ""
                for chunk in gemini_client.models.generate_content_stream(
                    model=MODEL,
                    contents=contents,
                    config=config,
                ):
                    if chunk.text:
                        full_text += chunk.text

                raw_text = full_text

                cleaned = strip_code_fence(full_text)

                try:
                    data = json.loads(cleaned)
                    parsed_json = json.dumps(data, ensure_ascii=False, indent=2)
                except json.JSONDecodeError as json_error:
                    error = f"JSON解析に失敗しました: {json_error}"

            except Exception as call_error:
                error = f"Gemma呼び出しエラー: {call_error}"

    return render_template_string(
        HTML,
        a=a,
        b=b,
        raw_text=raw_text,
        parsed_json=parsed_json,
        error=error,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
