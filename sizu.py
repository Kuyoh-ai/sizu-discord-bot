import yaml
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai import APIError, Timeout


def load_sizu_setting():
    with open("sizu_setting.yaml", "r", encoding="utf-8") as sizu_setting:
        return yaml.safe_load(sizu_setting)


sizu_setting = load_sizu_setting()
model = sizu_setting["model"]
system_prompt = sizu_setting["system_prompt"]
temperature = sizu_setting["temperature"]
frequency_penalty = sizu_setting["frequency_penalty"]
presence_penalty = sizu_setting["presence_penalty"]
max_tokens = sizu_setting["max_tokens"]
sizu_msg = sizu_setting["msg"]

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def isFlagged(prompt) -> bool:
    if not prompt:
        return False

    response = await client.moderations.create(input=prompt)
    flagged = response.results[0].flagged
    return flagged


async def chat(user_name, user_message, base64_images):
    # 空の場合
    if not user_message and len(base64_images) == 0:
        return sizu_msg["no_prompt"]

    # prompt構築
    prompt = f"ユーザー名:[{user_name}]\n{user_message}"

    # モデレーション
    if await isFlagged(prompt):
        prompt = f"ユーザー名:[{user_name}]\n<<検閲されたメッセージ>>"

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
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
        )
    except APIError as e:
        print(f"APIError: {e}")
        return sizu_msg["timeout"]
    except Timeout as e:
        print(f"Timeout: {e}")
        return sizu_msg["timeout"]

    return response.choices[0].message.content
