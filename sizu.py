import yaml
import os
import aiohttp
import json
from datetime import datetime
import pytz
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai import APIError, Timeout


def load_sizu_setting():
    with open("sizu_setting.yaml", "r", encoding="utf-8") as sizu_setting:
        return yaml.safe_load(sizu_setting)


sizu_setting = load_sizu_setting()
model = sizu_setting["model"]
system_prompt = sizu_setting["system_prompt"]
black_system_prompt = sizu_setting["black_system_prompt"]
# temperature = sizu_setting["temperature"]
# frequency_penalty = sizu_setting["frequency_penalty"]
# presence_penalty = sizu_setting["presence_penalty"]
max_tokens = sizu_setting["max_tokens"]
sizu_msg = sizu_setting["msg"]

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_idea_anniversary_tag",
            "description": "本日のお題を取得します。 e.g. 女の子, 桜",
            "parameters": {},
        },
    }
]

# **************************
# function calling
# **************************


# =========================
# get_idea_anniversary_tag: tool 用に content だけ返す
# =========================
async def get_idea_anniversary_tag(args):
    jst = pytz.timezone("Asia/Tokyo")
    today = datetime.now(jst).strftime("%Y-%m-%d")
    url = f"https://www.pixiv.net/ajax/idea/anniversary/{today}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    idea_anniversary_tag = data.get("body", {}).get(
                        "idea_anniversary_tag", None
                    )
                    # tool 結果は文字列（必要なら json.dumps でも可）
                    return json.dumps({"theme": idea_anniversary_tag})
                else:
                    print(f"Request failed with status code: {response.status}")
                    return json.dumps({"theme": None})
        except Exception as e:
            print(f"その他の例外：{e}")
            return json.dumps({"theme": None})


# =========================
# function_call: そのまま中身を返す（文字列）
# =========================
async def function_call(tool_call):
    func = globals().get(tool_call.function.name)
    if func and callable(func):
        # 引数が JSON 文字列のことがあるので一応パース試行
        raw = tool_call.function.arguments
        try:
            args = json.loads(raw) if isinstance(raw, str) and raw else {}
        except Exception:
            args = {}
        return await func(args)
    return ""


# **************************
# util
# **************************


async def isFlagged(prompt) -> bool:
    if not prompt:
        return False

    response = await client.moderations.create(input=prompt)
    flagged = response.results[0].flagged
    return flagged


# =========================
# completions: 返り値を統一
# =========================
async def completions(messages, tool_choice="auto"):
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        return resp.choices[0]  # 常に Choice を返す
    except (APIError, Timeout) as e:
        print(f"OpenAI error: {e}")
        return None  # ← 文字列を返さない


# =========================
# chat: 型ガード & 正しい tool 応答フロー
# =========================
async def chat(user_name, user_message, base64_images, black_flag=False):
    if not user_message and len(base64_images) == 0:
        return sizu_msg["no_prompt"]

    prompt = f"ユーザー名:{user_name}\n{user_message}"
    if await isFlagged(prompt):
        prompt = f"ユーザー名:{user_name}\n<<検閲されたメッセージ>>"

    user_content = [{"type": "text", "text": prompt}]
    for base64_image in base64_images or []:
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
            }
        )

    messages = [
        {
            "role": "system",
            "content": black_system_prompt if black_flag else system_prompt,
        },
        {"role": "user", "content": user_content},
    ]

    # 1st call
    choice = await completions(messages=messages)
    if choice is None:
        return sizu_msg["timeout"]  # ★ 型ガード

    # tool 呼び出しがある？
    if getattr(choice, "finish_reason", "") == "tool_calls":
        # assistant の tool_calls メッセージをまず積む
        assistant_msg = choice.message
        messages.append(assistant_msg)

        # 各 tool を実行して tool ロールで返す
        for tc in assistant_msg.tool_calls or []:
            tool_result_content = await function_call(tc)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result_content,  # 文字列
                }
            )

        # 2nd call（追加の tool 呼び出しは抑制）
        choice = await completions(messages=messages, tool_choice="none")
        if choice is None:
            return sizu_msg["timeout"]

    # 最終返答の取り出し（存在チェック）
    final_msg = getattr(choice, "message", None)
    if not final_msg or not getattr(final_msg, "content", None):
        return sizu_msg["timeout"]
    return final_msg.content
