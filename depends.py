import copy
import re
from typing import List

from meme_generator.meme import Meme
from hoshino import HoshinoBot
from hoshino.typing import CQEvent, MessageSegment, Message

from .config import (
    memes_use_sender_when_no_image,
    memes_use_default_when_no_text,
    meme_command_start
)
from .data_source import (
    ImageSource,
    ImageUrl,
    User,
    QQUser
)
from .utils import split_text


def restore_last_at_me_seg(event: CQEvent, msg: Message):
    def _is_at_me_seg(seg: MessageSegment):
        return seg.type == "at" and str(seg.data["qq"]) == str(event.self_id)

    if event.to_me:
        raw_msg = event.original_message
        i = -1
        last_msg_seg = raw_msg[i]
        if (
                last_msg_seg.type == "text"
                and not str(last_msg_seg.data["text"]).strip()
                and len(raw_msg) >= 2
        ):
            i -= 1
            last_msg_seg = raw_msg[i]

        if _is_at_me_seg(last_msg_seg):
            msg.append(last_msg_seg)


async def split_msg_v11(
        bot: HoshinoBot, event: CQEvent, msg: Message, meme: Meme, trigger: MessageSegment
) -> dict:
    texts: List[str] = []
    users: List[User] = []
    image_sources: List[ImageSource] = []
    trigger_text_with_trigger: str = trigger.data["text"].strip()
    if meme.patterns:
        rule = meme.patterns[0]
        origin: str = trigger_text_with_trigger.replace(meme_command_start, "")
        try:
            trigger_text = ""
            trigger_origin = re.search(rule, origin).groups()
            if r"吴京[\s:：]*(.*?)中国(.*)" == rule:
                if not trigger_origin[0]:
                    re_args = trigger_origin[1].split("中国")
                    re_args[0] = f"中国{re_args[0]}"
                    trigger_origin = re_args
            for each_group in trigger_origin:
                trigger_text += f"{each_group} "
        except AttributeError:
            trigger_text = origin
    else:
        trigger_text = re.sub(rf"^{meme_command_start}\S+", "", trigger_text_with_trigger)
    trigger_text_seg = Message(f"{trigger_text.strip()} ")
    msg.remove(trigger)
    msg: Message = trigger_text_seg.extend(msg)

    restore_last_at_me_seg(event, msg)

    for msg_seg in msg:
        if msg_seg.type == "at":
            image_sources.append(ImageUrl(event.avatar))
            users.append(QQUser(bot, event, int(msg_seg.data["qq"])))

        elif msg_seg.type == "image":
            image_sources.append(ImageUrl(url=msg_seg.data["url"]))

        elif msg_seg.type == "reply":
            msg_id = msg_seg.data["id"]
            source_msg = await bot.get_msg(message_id=int(msg_id))
            source_qq = str(source_msg['sender']['user_id'])
            source_msg = source_msg["message"]
            msgs = Message(source_msg)
            for each_msg in msgs:
                if each_msg.type == "image":
                    image_sources.append(ImageUrl(url=each_msg.data["url"]))
                    break
            else:
                image_sources.append(ImageUrl(event.avatar))
                users.append(QQUser(bot, event, int(source_qq)))

        elif msg_seg.type == "text":
            raw_text = msg_seg.data["text"]
            split_msg = split_text(raw_text)
            for text in split_msg:
                if text == "自己":
                    image_sources.append(
                        ImageUrl(event.avatar)
                    )
                    users.append(QQUser(bot, event, event.user_id))

                else:
                    texts.append(text)

    # 当所需图片数为 2 且已指定图片数为 1 时，使用 发送者的头像 作为第一张图
    if meme.params_type.min_images == 2 and len(image_sources) == 1:
        image_sources.insert(0, ImageUrl(event.avatar))
        users.insert(0, QQUser(bot, event, event.user_id))

    # 当所需图片数为 1 且没有已指定图片时，使用发送者的头像
    if memes_use_sender_when_no_image and (
            meme.params_type.min_images == 1 and len(image_sources) == 0
    ):
        image_sources.append(ImageUrl(event.avatar))
        users.append(QQUser(bot, event, event.user_id))

    # 当所需文字数 >0 且没有输入文字时，使用默认文字
    if memes_use_default_when_no_text and (
            meme.params_type.min_texts > 0 and len(texts) == 0
    ):
        texts = meme.params_type.default_texts

    # 当所需文字数 > 0 且没有输入文字，且仅存在一个参数时，使用默认文字
    # 为了防止误触发，参数必须放在最后一位，且该参数必须是bool，且参数前缀必须是--
    if memes_use_default_when_no_text and (
            meme.params_type.min_texts > 0 and len(texts) == 1 and texts[-1].startswith("--")
    ):
        temp = copy.deepcopy(meme.params_type.default_texts)
        temp.extend(texts)
        texts = temp
    return {
        "texts": texts,
        "users": users,
        "image_sources": image_sources
    }
