import asyncio
import base64
import copy
import hashlib
import os
import random
import traceback
import json
from io import BytesIO
from itertools import chain
from pathlib import Path
from typing import List, Union, Dict, Any
from hoshino.image_host import upload_img
from hoshino.MD import *
from aiocqhttp.exceptions import ActionFailed
from hoshino import HoshinoBot, Service, priv
from hoshino.aiorequests import run_sync_func
from hoshino.typing import CQEvent, MessageSegment, Message
from ..groupmaster.switch import sdb
from meme_generator.download import check_resources
from meme_generator.exception import (
    TextOverLength,
    ArgMismatch,
    TextOrNameNotEnough,
    MemeGeneratorException,
    ArgParserExit
)
from meme_generator.meme import Meme
from meme_generator.utils import TextProperties, render_meme_list
from pypinyin import Style, pinyin

from .config import memes_prompt_params_error, meme_command_start
from .data_source import ImageSource, User, UserInfo
from .depends import split_msg_v11
from .exception import NetworkError, PlatformUnsupportError
from .manager import ActionResult, MemeMode, meme_manager
from .utils import meme_info

memes_cache_dir = Path(os.path.join(os.path.dirname(__file__), "memes_cache_dir"))

sv_help = """
[表情包制作] 发送全部功能帮助
[表情帮助 + 表情] 发送选定表情功能帮助
[更新表情包制作] 更新表情包资源
"""

sv = Service(
    name="头像表情包",
    use_priv=priv.NORMAL,  # 使用权限
    manage_priv=priv.ADMIN,  # 管理权限
    visible=True,  # False隐藏
    enable_on_default=True,  # 是否默认启用
    bundle='娱乐',  # 属于哪一类
    help_=sv_help  # 帮助文本
)

def bytesio2b64(img: Union[BytesIO, bytes]) -> str:
    if isinstance(img, BytesIO):
        img = img.getvalue()
    return f"base64://{base64.b64encode(img).decode()}"


def get_user_id(ev: CQEvent, permit: Union[int, None] = None):
    if permit is None or permit < 21:
        cid = f"{ev.self_id}_{ev.group_id}_{ev.user_id}"
    else:
        cid = f"{ev.self_id}_{ev.group_id}"
    return cid

@sv.on_fullmatch(("表情包制作", "头像表情包", "文字表情包"))
async def help_cmd(bot: HoshinoBot, ev: CQEvent):
    status = sdb.get_status(ev.real_group_id,'头像表情包')
    if not status:
        return
    memes = sorted(
        meme_manager.memes,
        key=lambda meme: "".join(
            chain.from_iterable(pinyin(meme.keywords[0], style=Style.TONE3))
        ),
    )
    user_id = get_user_id(ev)
    meme_list = [
        (
            meme,
            TextProperties(
                fill="black" if meme_manager.check(user_id, meme.key) else "lightgrey"
            ),
        )
        for meme in memes
    ]

    # cache rendered meme list
    meme_list_hashable = [
        ({"key": meme.key, "keywords": meme.keywords}, prop) for meme, prop in meme_list
    ]
    meme_list_hash = hashlib.md5(str(meme_list_hashable).encode("utf8")).hexdigest()
    meme_list_cache_file = memes_cache_dir / f"{meme_list_hash}.jpg"
    if not meme_list_cache_file.exists():
        img: BytesIO = await run_sync_func(render_meme_list, meme_list)
        with open(meme_list_cache_file, "wb") as f:
            f.write(img.getvalue())
    else:
        img = BytesIO(meme_list_cache_file.read_bytes())
    img_url = await upload_img(img)
    data = ['头像表情包功能','img#500px #500px',img_url,'触发方式：<@古贺小春 + 关键词 + 图片/文字>\r发送 <表情详情 + 关键词> 查看表情参数和预览\r图片参数不足时将会使用发送者的头像\r暂不支持@他人作为图片参数\r','目前支持的表情列表见上图']
    b = button_gen
    buttons = [b('试一试',' '),b('表情详情',' '),b('帮助','https://www.koharu.cn/docs/tool/tool.html#%E5%A4%B4%E5%83%8F%E8%A1%A8%E6%83%85%E5%8C%85',type_int=0)]
    buttons = generate_buttons(buttons)
    msg = generate_md(2,data,buttons)
    await bot.send(ev, msg)

@sv.on_prefix(("表情帮助", "表情示例", "表情详情"))
async def info_cmd(bot: HoshinoBot, ev: CQEvent):
    status = sdb.get_status(ev.real_group_id,'头像表情包')
    if not status:
        return
    meme_name = ev.message.extract_plain_text().strip()
    if not meme_name:
        await bot.finish(ev, "参数出错，请重新输入")

    if not (meme := meme_manager.find(meme_name)):
        await bot.finish(ev, f"表情 {meme_name} 不存在！")

    info = meme_info(meme)
    info += "表情预览：\n"
    img = await meme.generate_preview()

    await bot.finish(ev, info + MessageSegment.image(bytesio2b64(img)))

async def process(
        bot: HoshinoBot,
        ev: CQEvent,
        meme: Meme,
        image_sources: List[ImageSource],
        texts: List[str],
        users: List[User],
        args=None,
):
    if args is None:
        args = {}
    images: List[bytes] = []
    user_infos: List[UserInfo] = []

    try:
        for image_source in image_sources:
            images.append(await image_source.get_image())
    except PlatformUnsupportError as e:
        await bot.finish(ev, f"当前平台 “{e.platform}” 暂不支持获取头像，请使用图片输入")
    except NetworkError:
        sv.logger.warning(traceback.format_exc())
        await bot.finish(ev, "图片下载出错，请稍后再试")

    try:
        for user in users:
            user_infos.append(await user.get_info())
        args["user_infos"] = user_infos
    except NetworkError:
        sv.logger.warning("用户信息获取失败\n" + traceback.format_exc())

    try:
        result = await meme(images=images, texts=texts, args=args)
        try:
            await bot.send(ev, MessageSegment.image(bytesio2b64(result)))
        except ActionFailed:
            await bot.send(ev, "发送失败……消息可能被风控")

    except TextOverLength as e:
        await bot.send(ev, f"文字 “{e.text}” 长度过长")
    except ArgMismatch:
        await bot.send(ev, "参数解析错误")
    except TextOrNameNotEnough:
        await bot.send(ev, "文字或名字数量不足")
    except MemeGeneratorException:
        sv.logger.warning(traceback.format_exc())
        await bot.send(ev, "出错了，请稍后再试")
    except ValueError as e:
        sv.logger.warning(traceback.format_exc())
        await bot.send(ev, e.args[0])


async def find_meme(
        trigger: str, raw_trigger: str, bot: HoshinoBot, ev: CQEvent
) -> Union[Meme, None]:
    if trigger == "随机表情":
        meme = random.choice(meme_manager.memes)
        uid = get_user_id(ev)
        if not meme_manager.check(uid, meme.key):
            await bot.send(ev, "随机到的表情不可用了捏qwq\n再试一次吧~")
            return None

        await bot.send(ev, f"随机到了【{meme.keywords[0]}】")
        return meme
    meme = meme_manager.find(trigger)
    if meme is None:
        meme = meme_manager.find(raw_trigger)
    return meme


@sv.on_message('group')
async def handle(bot: HoshinoBot, ev: CQEvent):
    status = sdb.get_status(ev.real_group_id,'头像表情包')
    if not status:
        return
    msg: Message = copy.deepcopy(ev.message)
    if not msg:
        # sv.logger.info("Empty msg, skip")
        return
    if msg[0].type == "reply":
        # 当回复目标是自己时，去除隐式at自己
        msg_id = msg[0].data["id"]
        source_msg = await bot.get_msg(message_id=int(msg_id))
        source_qq = str(source_msg['sender']['user_id'])
        # 隐式at和显示at之间还有一个文本空格
        while len(msg) > 1 and (
                msg[1].type == 'at' or msg[1].type == 'text' and msg[1].data['text'].strip() == ""):
            if msg[1].type == 'at' and msg[1].data['qq'] == source_qq:
                msg.pop(1)
            elif msg[1].type == 'text' and msg[1].data['text'].strip() == "":
                msg.pop(1)
                break
            else:
                break
    for each_msg in msg:
        if not each_msg.type == "text":
            continue
        if not each_msg.data["text"].strip().startswith(meme_command_start):
            continue
        trigger = each_msg
        break
    else:
        for each_msg in msg:
            if not each_msg.type == "text":
                continue
            trigger = each_msg
            break
        else:
            # sv.logger.info("Empty trigger, skip")
            return

    uid = get_user_id(ev)
    try:
        trigger_text: str = trigger.data["text"].split()[0]
        raw_trigger_text: str = trigger.data["text"].strip()
    except IndexError:
        # sv.logger.info("Empty trigger, skip")
        return
    if not trigger_text.startswith(meme_command_start):
        # sv.logger.info("Empty prefix, skip")
        return
    meme = await find_meme(
        trigger_text.replace(meme_command_start, "").strip(),
        raw_trigger_text.replace(meme_command_start, "").strip(),
        bot, ev
    )
    if meme is None:
        # sv.logger.info("Empty meme, skip")
        return
    if not meme_manager.check(uid, meme.key):
        # sv.logger.info("Blocked meme, skip")
        return

    split_msg = await split_msg_v11(bot, ev, msg, meme, trigger)

    raw_texts: List[str] = split_msg["texts"]
    users: List[User] = split_msg["users"]
    image_sources: List[ImageSource] = split_msg["image_sources"]

    args: Dict[str, Any] = {}

    if meme.params_type.args_type:
        try:
            parse_result = meme.parse_args(raw_texts)
        except ArgParserExit:
            await bot.send(ev, f"参数解析错误")
            return
        texts = parse_result["texts"]
        parse_result.pop("texts")
        args = parse_result
    else:
        texts = raw_texts

    if not (
            meme.params_type.min_images
            <= len(image_sources)
            <= meme.params_type.max_images
    ):
        if meme.params_type.min_images > len(image_sources):
            if memes_prompt_params_error:
                await bot.send(
                    ev,
                    f"输入图片数量过少，图片数量至少为 {meme.params_type.min_images}" + f", 实际数量为{len(image_sources)}"
                )
            return
        else:
            image_sources = image_sources[:meme.params_type.max_images]

    if not (meme.params_type.min_texts <= len(texts) <= meme.params_type.max_texts):
        if memes_prompt_params_error:
            await bot.send(
                ev,
                f"输入文字数量不符，文字数量应为 {meme.params_type.min_texts}"
                + (
                    f" ~ {meme.params_type.max_texts}"
                    if meme.params_type.max_texts > meme.params_type.min_texts
                    else ""
                ) + f", 实际数量为{len(raw_texts)}"
            )
        return

    await process(bot, ev, meme, image_sources, texts, users, args)


@sv.on_fullmatch(("更新表情包制作", "更新头像表情包", "更新文字表情包"))
async def update_res(bot: HoshinoBot, ev: CQEvent):
    sv.logger.info("正在检查资源文件...")
    try:
        asyncio.create_task(check_resources())
    except Exception as e:
        await bot.send(ev, f"更新资源出错：\n{e}")
        return
    await bot.send(ev, f"更新资源完成")
