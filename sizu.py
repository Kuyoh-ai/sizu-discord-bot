import yaml
import os
import aiohttp
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
temperature = sizu_setting["temperature"]
frequency_penalty = sizu_setting["frequency_penalty"]
presence_penalty = sizu_setting["presence_penalty"]
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


# pixiv 本日のテーマの取得
async def get_idea_anniversary_tag():
    jst = pytz.timezone("Asia/Tokyo")
    today = datetime.now(jst).strftime("%Y-%m-%d")
    url = f"https://www.pixiv.net/ajax/idea/anniversary/{today}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    # awaitを追加して非同期処理を待ちます。
                    data = await response.json()
                    idea_anniversary_tag = data.get("body", {}).get(
                        "idea_anniversary_tag", None
                    )
                    return {"theme": idea_anniversary_tag}
                else:
                    # status_codeではなくstatusを使用する
                    print(f"Request failed with status code: {response.status}")
                    return None
        except Exception as e:
            print(f"その他の例外：{e}")
            return None


async def isFlagged(prompt) -> bool:
    if not prompt:
        return False

    response = await client.moderations.create(input=prompt)
    flagged = response.results[0].flagged
    return flagged


async def completions(messages, tool_choice="auto"):
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )
    except APIError as e:
        print(f"APIError: {e}")
        return sizu_msg["timeout"]
    except Timeout as e:
        print(f"Timeout: {e}")
        return sizu_msg["timeout"]
    return response.choices[0]


async def chat(user_name, user_message, base64_images, black_flag=False):
    # 空の場合
    if not user_message and len(base64_images) == 0:
        return sizu_msg["no_prompt"]

    # prompt構築
    prompt = f"ユーザー名:{user_name}\n{user_message}"

    # モデレーション
    if await isFlagged(prompt):
        prompt = f"ユーザー名:{user_name}\n<<検閲されたメッセージ>>"

    # messages構築
    user_content = [
        {"type": "text", "text": prompt},
    ]
    if len(base64_images) > 0:
        for base64_image in base64_images:
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
    # チャット呼び出し
    response_message = await completions(messages=messages)

    # ツール呼び出しが行われた場合
    if response_message.finish_reason == "tool_calls":
        theme = await get_idea_anniversary_tag()
        messages.append(
            {
                "role": "function",
                "content": str(theme),
                "name": "get_idea_anniversary_tag",
            }
        )
        messages.append(
            {
                "role": "system",
                "content": "受け取ったthemeから連想される話題を話してください",
            }
        )
        response_message = await completions(messages=messages, tool_choice="none")
    return getattr(
        getattr(response_message, "message", sizu_msg["timeout"]),
        "content",
        sizu_msg["timeout"],
    )
