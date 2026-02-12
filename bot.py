import asyncio
import datetime
import logging
import os
from typing import Iterable, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    Chat,
    ChatMember,
    ErrorEvent,
    Message,
    User,
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OWNER_ID_RAW = os.getenv("OWNER_ID")
try:
    OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW else None
except ValueError:
    OWNER_ID = None
    logger.warning("OWNER_ID задан, но не является числом: %s", OWNER_ID_RAW)


def yes_no(value: Optional[bool]) -> str:
    if value is True:
        return "да"
    if value is False:
        return "нет"
    return "неизвестно"


async def notify_owner(bot: Bot, text: str) -> None:
    """Send a short notification to the owner if OWNER_ID is configured."""
    if not OWNER_ID:
        return
    try:
        await bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception:  # noqa: BLE001 - важно не упасть в цикле уведомлений
        logger.exception("Не удалось отправить уведомление владельцу")


def format_user(user: User) -> str:
    """Render as much info as Bot API exposes for the User object."""

    fields: list[str] = [
        f"ID: {user.id}",
        f"Bot: {yes_no(user.is_bot)}",
        f"Имя: {user.full_name}",
        f"Username: @{user.username}" if user.username else "Username: нет",
        f"Язык: {user.language_code or 'неизвестно'}",
        f"Premium: {yes_no(getattr(user, 'is_premium', None))}",
        f"Scam: {yes_no(getattr(user, 'is_scam', None))}",
        f"Fake: {yes_no(getattr(user, 'is_fake', None))}",
        f"Support: {yes_no(getattr(user, 'is_support', None))}",
        f"Добавлен в меню вложений: {yes_no(getattr(user, 'added_to_attachment_menu', None))}",
        f"Может присоединяться к группам: {yes_no(getattr(user, 'can_join_groups', None))}",
        f"Может читать все сообщения групп: {yes_no(getattr(user, 'can_read_all_group_messages', None))}",
        f"Поддерживает inline: {yes_no(getattr(user, 'supports_inline_queries', None))}",
        f"Can connect to business: {yes_no(getattr(user, 'can_connect_to_business', None))}",
        f"Has main web app: {yes_no(getattr(user, 'has_main_web_app', None))}",
    ]

    if getattr(user, "emoji_status_custom_emoji_id", None):
        fields.append(f"Emoji статус: {user.emoji_status_custom_emoji_id}")
    if getattr(user, "personal_chat_id", None):
        fields.append(f"Personal chat ID: {user.personal_chat_id}")

    return "\n".join(fields)


def format_admins(admins: Iterable[ChatMember], limit: int = 6) -> str:
    admins = list(admins)
    if not admins:
        return "Администраторы: недоступно"

    shown = admins[:limit]
    rendered = ", ".join(f"{m.user.full_name} (id {m.user.id})" for m in shown)
    if len(admins) > limit:
        rendered += f", и еще {len(admins) - limit}"
    return f"Администраторы ({len(admins)}): {rendered}"


def format_permissions(chat: Chat) -> str:
    perms = chat.permissions
    if not perms:
        return "Разрешения по умолчанию: недоступно"

    allowed = []
    mapping = {
        "can_send_messages": "сообщения",
        "can_send_audios": "аудио",
        "can_send_documents": "документы",
        "can_send_photos": "фото",
        "can_send_videos": "видео",
        "can_send_video_notes": "видео-заметки",
        "can_send_voice_notes": "голосовые",
        "can_send_polls": "опросы",
        "can_send_other_messages": "другое",
        "can_add_web_page_previews": "превью ссылок",
        "can_change_info": "изменять инфо",
        "can_invite_users": "приглашать",
        "can_pin_messages": "пинить",
        "can_manage_topics": "управлять топиками",
    }
    for attr, label in mapping.items():
        if getattr(perms, attr, None):
            allowed.append(label)
    if not allowed:
        return "Разрешения по умолчанию: запрещено все"
    return "Разрешения по умолчанию: " + ", ".join(allowed)


def extract_custom_emoji_ids(message: Message) -> list[str]:
    entities = []
    if message.entities:
        entities.extend(message.entities)
    if message.caption_entities:
        entities.extend(message.caption_entities)

    ids: list[str] = []
    for entity in entities:
        entity_type = getattr(entity.type, "value", entity.type)
        if entity_type == "custom_emoji":
            custom_id = getattr(entity, "custom_emoji_id", None)
            if custom_id:
                ids.append(custom_id)
    return ids


async def safe_get_member_count(bot: Bot, chat_id: int) -> Optional[int]:
    try:
        return await bot.get_chat_member_count(chat_id)
    except TelegramForbiddenError:
        logger.info("Нет прав читать количество участников")
    except TelegramBadRequest:
        logger.info("Не удалось получить количество участников")
    return None


async def safe_get_admins(bot: Bot, chat_id: int) -> list[ChatMember]:
    try:
        return await bot.get_chat_administrators(chat_id)
    except TelegramForbiddenError:
        logger.info("Нет прав читать администраторов")
    except TelegramBadRequest:
        logger.info("Не удалось получить администраторов")
    return []


async def build_chat_info(
    bot: Bot,
    chat_id: int,
    thread_id: Optional[int] = None,
    is_topic_message: bool = False,
    message: Optional[Message] = None,
) -> str:
    chat = await bot.get_chat(chat_id)
    member_count = await safe_get_member_count(bot, chat_id)
    admins = await safe_get_admins(bot, chat_id)

    chat_type = chat.type.value if hasattr(chat.type, "value") else str(chat.type)

    lines = [
        f"ID: {chat.id}",
        f"Тип: {chat_type}",
    ]
    if chat.title:
        lines.append(f"Название: {chat.title}")
    if chat.username:
        lines.append(f"Публичное имя: @{chat.username}")
    if chat.description:
        lines.append(f"Описание: {chat.description}")
    if chat.bio:
        lines.append(f"Био: {chat.bio}")
    if chat.invite_link:
        lines.append(f"Инвайт-линк: {chat.invite_link}")

    lines.extend(
        [
            f"Защита контента: {yes_no(chat.has_protected_content)}",
            f"Скрытые участники: {yes_no(chat.has_hidden_members)}",
            f"Private forwards: {yes_no(chat.has_private_forwards)}",
            f"Aggressive anti-spam: {yes_no(chat.has_aggressive_anti_spam_enabled)}",
            f"Join-to-send: {yes_no(chat.join_to_send_messages)}",
            f"Join-by-request: {yes_no(chat.join_by_request)}",
            f"Форум включен: {yes_no(chat.is_forum)}",
        ]
    )

    if chat.linked_chat_id:
        lines.append(f"Связанный чат ID: {chat.linked_chat_id}")
    if chat.active_usernames:
        lines.append("Доп. юзернеймы: " + ", ".join(f"@{u}" for u in chat.active_usernames))

    if member_count is not None:
        lines.append(f"Участников: {member_count}")

    lines.append(format_permissions(chat))
    lines.append(format_admins(admins))

    if chat.is_forum:
        if is_topic_message and thread_id:
            topic_info = await fetch_topic_info(bot, chat_id, thread_id, message)
            lines.extend(topic_info)
        else:
            lines.append(
                "Форумный режим включен. Запустите /info внутри конкретного топика, "
                "чтобы вывести сведения о нем."
            )

    return "\n".join(lines)


async def fetch_topic_info(
    bot: Bot,
    chat_id: int,
    thread_id: int,
    message: Optional[Message] = None,
) -> list[str]:
    """Fetch and format info about a single forum topic.

    Bot API не позволяет получить список всех топиков, поэтому берем данные
    по thread_id той ветки, где вызвали команду.
    """

    topic_data = None

    # aiogram 3.4.1 не имеет get_forum_topic, а низкоуровневый вызов требует
    # TelegramMethod объекта. Чтобы не падать, используем только данные из сообщения.
    if message and message.reply_to_message:
        created = message.reply_to_message.forum_topic_created
        if created:
            topic_data = {
                "name": created.name,
                "icon_color": created.icon_color,
                "icon_custom_emoji_id": getattr(created, "icon_custom_emoji_id", None),
            }

    if topic_data is None:
        return [f"Топик id {thread_id}: не удалось загрузить детали"]

    name = topic_data.get("name")
    icon_color = topic_data.get("icon_color")
    icon_emoji = topic_data.get("icon_custom_emoji_id")

    info = [
        f"Топик: {name or 'без имени'}",
        f"Thread ID: {thread_id}",
    ]
    if icon_color is not None:
        info.append(f"Цвет иконки: {icon_color}")
    if icon_emoji:
        info.append(f"Emoji иконки: {icon_emoji}")
    return info


async def handle_error(event: ErrorEvent, bot: Bot) -> bool:
    """Global error handler: не даем боту упасть и шлем уведомление владельцу."""

    exc = event.exception
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    update_dump = ""
    if event.update:
        try:
            if hasattr(event.update, "model_dump"):
                raw = event.update.model_dump(exclude_none=True, by_alias=True)
            elif hasattr(event.update, "dict"):
                raw = event.update.dict(exclude_none=True)
            else:
                raw = str(event.update)
            update_dump = f"Update: {raw}"
        except Exception:
            logger.debug("Не удалось сериализовать update для отчета", exc_info=True)

    preview = update_dump
    if len(preview) > 1500:
        preview = preview[:1500] + "…"

    report = "\n".join(
        part for part in [
            f"⚠️ Ошибка {timestamp}",
            f"{type(exc).__name__}: {exc}",
            preview,
        ] if part
    )

    await notify_owner(bot, report)
    logger.exception("Исключение в обработчике", exc_info=exc)
    return True


async def set_commands(bot: Bot) -> None:
    private_commands = [
        BotCommand(command="start", description="Показать справку"),
        BotCommand(command="info", description="Информация о себе"),
    ]
    group_commands = [BotCommand(command="info", description="Информация о чате")]

    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllChatAdministrators())


async def start_private(message: Message) -> None:
    text = (
        "Привет! Я вывожу информацию о пользователях, группах и каналах.\n\n"
        "/start — эта подсказка.\n"
        "/info — показать все, что бот знает о тебе в личке.\n\n"
        "Отправь кастомную emoji из Premium-набора в личку — верну ее ID.\n\n"
        "Если добавить меня в группу, супер-группу или канал, отправьте /info —"
        " я верну максимум сведений о чате. В форумах команду лучше писать внутри"
        " конкретного топика, чтобы показать его thread_id."
    )
    await message.answer(text)


async def info_private(message: Message) -> None:
    if not message.from_user:
        await message.answer("Не удалось получить данные пользователя")
        return
    text = format_user(message.from_user)
    await message.answer(text)


async def info_group(message: Message, bot: Bot) -> None:
    text = await build_chat_info(
        bot=bot,
        chat_id=message.chat.id,
        thread_id=message.message_thread_id,
        is_topic_message=bool(message.is_topic_message),
        message=message,
    )
    await message.answer(text)


async def info_channel(message: Message, bot: Bot) -> None:
    text = await build_chat_info(bot=bot, chat_id=message.chat.id)
    await message.answer(text)


async def custom_emoji_id_private(message: Message) -> None:
    ids = extract_custom_emoji_ids(message)
    if not ids:
        return

    unique_ids = list(dict.fromkeys(ids))
    if len(unique_ids) == 1:
        text = f"ID кастомной emoji: {unique_ids[0]}"
    else:
        text = "ID кастомных emoji:\n" + "\n".join(unique_ids)
    await message.answer(text)


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не найден в окружении")

    bot = Bot(token=token)
    dp = Dispatcher()

    # Register handlers
    dp.message.register(start_private, CommandStart(), F.chat.type == ChatType.PRIVATE)
    dp.message.register(info_private, Command("info"), F.chat.type == ChatType.PRIVATE)
    dp.message.register(custom_emoji_id_private, F.chat.type == ChatType.PRIVATE)
    dp.message.register(
        info_group,
        Command("info"),
        F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    )
    dp.message.register(info_channel, Command("info"), F.chat.type == ChatType.CHANNEL)
    dp.channel_post.register(info_channel, Command("info"))  # команды из постов канала
    dp.errors.register(handle_error)

    await bot.delete_webhook(drop_pending_updates=True)
    await set_commands(bot)

    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C")
            break
        except Exception as exc:  # noqa: BLE001 - хотим перезапускать при любой ошибке
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            msg = f"⚠️ Polling упал {timestamp}\n{type(exc).__name__}: {exc}"
            await notify_owner(bot, msg)
            logger.exception("Polling упал, перезапуск через 5 секунд", exc_info=exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
