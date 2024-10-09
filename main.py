import discord
import asyncio
import aiohttp
import json
import os
import base64
import datetime
import re
from dotenv import load_dotenv
from PIL import Image, ImageOps
from io import BytesIO

from sizu import chat


def load_config():
    with open("config.json", "r", encoding="utf-8") as config_file:
        return json.load(config_file)


load_dotenv()
config = load_config()
target_channel_names = config["target_channel_names"]
reaction_emoji = config["reaction_emoji"]
black_id_list = config["black_id_list"]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


async def load_attachments_images(attachments):
    processed_image_count = 0
    images = []
    async with aiohttp.ClientSession() as session:
        for attachment in attachments:
            if processed_image_count >= 2:
                break

            if any(
                attachment.filename.lower().endswith(ext)
                for ext in [".png", ".jpg", ".jpeg", ".gif"]
            ):
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        image = Image.open(BytesIO(data))

                        # 最も長い辺が512px未満の場合、リサイズを行わない
                        if image.width > 512 or image.height > 512:
                            max_size = (512, 512)
                            image.thumbnail(max_size, Image.LANCZOS)

                        # 透過情報がある場合、白色で背景を塗りつぶし
                        if image.mode in ["RGBA", "LA"]:
                            background = Image.new("RGB", image.size, (255, 255, 255))
                            background.paste(
                                image, mask=image.split()[3]
                            )  # 3はアルファチャネル
                            image = background
                        elif image.mode == "P":
                            image = ImageOps.colorize(
                                image.convert("L"), (0, 0, 0), (255, 255, 255)
                            )

                        # JPEG形式で画像を保存し、Base64でエンコード
                        buffered = BytesIO()
                        image.save(buffered, format="JPEG")
                        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
                        images.append(img_str)
                        processed_image_count += 1
    return images


async def load_images(attachments, stickers):
    processed_image_count = 0
    images = []
    async with aiohttp.ClientSession() as session:
        # 添付ファイルの処理
        for attachment in attachments:
            if processed_image_count >= 2:
                break
            if any(
                attachment.filename.lower().endswith(ext)
                for ext in [".png", ".jpg", ".jpeg", ".gif"]
            ):
                image_data = await fetch_and_process_image(session, attachment.url)
                if image_data:
                    images.append(image_data)
                    processed_image_count += 1

        # ステッカーの処理
        for sticker in stickers:
            if processed_image_count >= 2:
                break
            image_data = await fetch_and_process_image(session, sticker.url)
            if image_data:
                images.append(image_data)
                processed_image_count += 1

    return images


async def fetch_and_process_image(session, url):
    async with session.get(url) as resp:
        if resp.status == 200:
            data = await resp.read()
            image = Image.open(BytesIO(data))

            if image.width > 512 or image.height > 512:
                max_size = (512, 512)
                image.thumbnail(max_size, Image.LANCZOS)

            if image.mode in ["RGBA", "LA"]:
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[3])
                image = background
            elif image.mode == "P":
                image = ImageOps.colorize(
                    image.convert("L"), (0, 0, 0), (255, 255, 255)
                )

            buffered = BytesIO()
            image.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            return img_str
    return None


@client.event
async def on_ready():
    print(f"ログインしました: {client.user}")


# キャッシュ用の辞書を追加
bot_messages = {}  # ボットのメッセージID: リプライ先のメッセージID
user_messages = {}  # メッセージID: (内容, タイムスタンプ, 送信者名)


def clean_up_cache():
    current_time = datetime.datetime.now()
    one_hour_ago = current_time - datetime.timedelta(hours=1)
    # 古いエントリを削除
    keys_to_remove = [
        msg_id
        for msg_id, (content, timestamp, author_name) in user_messages.items()
        if timestamp <= one_hour_ago
    ]
    for msg_id in keys_to_remove:
        del user_messages[msg_id]
    # user_messagesに存在しないリプライ先を持つボットのメッセージを削除
    bot_msgs_to_remove = [
        bot_msg_id
        for bot_msg_id, orig_msg_id in bot_messages.items()
        if orig_msg_id not in user_messages
    ]
    for bot_msg_id in bot_msgs_to_remove:
        del bot_messages[bot_msg_id]


# メンションを無効化する関数を修正
def disable_mentions(content):
    zero_width_space = "\u200b"
    # ユーザーメンションを無効化
    content = re.sub(r"<@!?(\d+)>", f"<@{zero_width_space}\\1>", content)
    # ロールメンションを無効化
    content = re.sub(r"<@&(\d+)>", f"<@&{zero_width_space}\\1>", content)
    # チャンネルメンションを無効化
    content = re.sub(r"<#(\d+)>", f"<#{zero_width_space}\\1>", content)
    return content


@client.event
async def on_message(message):
    # キャッシュのクリーンアップ
    clean_up_cache()

    # ボットのメッセージは無視
    if message.author.bot:
        return

    # メッセージをキャッシュ
    user_messages[message.id] = (
        message.content,
        datetime.datetime.now(),
        message.author.display_name,
    )

    # チャンネル名による制御
    channel_name = message.channel.name
    if target_channel_names is not None and len(target_channel_names) > 0:
        # 対象のチャンネル名をいずれも含まない場合return
        if all(name not in channel_name for name in target_channel_names):
            return

    if client.user.mentioned_in(message):
        # メッセージにリアクションを付ける
        await message.add_reaction(reaction_emoji or "✔")
        # 入力中...表示
        async with message.channel.typing():
            # ユーザーID, 名前
            user_id = message.author.id
            user_name = message.author.display_name
            # ブラックリスト調査
            black_flag = str(user_id) in black_id_list
            # メッセージのコンテンツからメンションを削除します。
            # clean_contentはメンションをユーザー名に置換しますが、ここでは完全に取り除きます。
            content_without_mentions = message.content
            for mention in message.mentions:
                content_without_mentions = content_without_mentions.replace(
                    mention.mention, ""
                ).strip()

            # 画像が添付された場合の前処理
            attachments = message.attachments
            base64_images = []
            if len(attachments) > 0:
                base64_images = await load_attachments_images(attachments)

            response = await chat(
                user_name, content_without_mentions, base64_images, black_flag
            )
        # サーバーからの応答としてメッセージを送信する
        reply_message = await message.reply(response)
        # リプライ関係をキャッシュ
        bot_messages[reply_message.id] = message.id

    # メンション以外のメッセージに対する処理
    else:
        # メッセージがスティッカー
        if len(message.stickers) > 0 and message.stickers[0] is not None:
            previous_messages = []
            async for previous_message in message.channel.history(limit=3):
                if previous_message.id != message.id:
                    previous_messages.append(previous_message)
            if previous_messages:
                # 過去のメッセージが全てスタンプ
                is_same_sticker = (
                    lambda msg: not msg.author.bot
                    and len(msg.stickers) > 0
                    and msg.stickers[0] is not None
                )
                if (
                    all([is_same_sticker(msg) for msg in previous_messages])
                    and message.stickers[0] is not None
                ):
                    if len(message.stickers) > 0:
                        sticker = message.stickers[0]
                        sticker_name = sticker.name
                        if all(
                            [
                                sticker.id == p_msg.stickers[0].id
                                for p_msg in previous_messages
                            ]
                        ):
                            await message.channel.send(stickers=[sticker])
                            # 入力中...表示
                            async with message.channel.typing():
                                # 名前
                                user_name = message.author.display_name

                                base64_images = await load_images([], [sticker])
                                response = await chat(
                                    user_name, sticker_name, base64_images, False
                                )
                            # サーバーからの応答としてメッセージを送信する
                            await message.channel.send(response)


@client.event
async def on_message_delete(message):
    # キャッシュのクリーンアップ
    clean_up_cache()

    # 削除されたメッセージがキャッシュに存在するか確認
    if message.id in user_messages:
        # ボットのメッセージでリプライ先が削除されたメッセージの場合
        for bot_msg_id, original_msg_id in bot_messages.items():
            if original_msg_id == message.id:
                try:
                    bot_message = await message.channel.fetch_message(bot_msg_id)
                except discord.NotFound:
                    continue  # ボットのメッセージが見つからない場合
                # 削除されたメッセージの内容と送信者名を取得
                deleted_message_content, _, author_name = user_messages[message.id]
                if not deleted_message_content:
                    return  # 内容が取得できない場合は中断

                # メンションを無効化
                deleted_message_content = disable_mentions(deleted_message_content)

                # 引用の作成
                quote_content = f"[{author_name}]\n{deleted_message_content}"
                quote_lines = quote_content.splitlines()
                quote = "\n".join(f"> {line}" for line in quote_lines) + "\n"

                # ボットのメッセージを編集して引用を追加
                new_content = f"{quote}\n{bot_message.content}"
                await bot_message.edit(content=new_content)
                # 処理済みのメッセージを辞書から削除
                del bot_messages[bot_msg_id]
                break  # 一つのボットメッセージのみを処理

        # 削除されたメッセージをキャッシュから削除
        del user_messages[message.id]


TOKEN = os.getenv("SIZU_BOT_TOKEN")
client.run(TOKEN)
