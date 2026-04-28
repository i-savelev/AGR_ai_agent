"""Утилиты для экспорта истории Telegram-чата в Markdown."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import re
from typing import Sequence

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.custom.message import Message


def _normalize_chat_reference(chat_reference: str) -> str:
    """Приводит ссылку или имя чата к формату, понятному Telethon.

    :param chat_reference: Имя пользователя чата, ссылка ``https://t.me/...`` или
        строка вида ``@username``.
    :returns: Нормализованное имя пользователя или исходная строка без пробелов.
    """

    normalized_reference = chat_reference.strip()
    if normalized_reference.startswith("https://t.me/"):
        normalized_reference = normalized_reference.removeprefix("https://t.me/")
    elif normalized_reference.startswith("http://t.me/"):
        normalized_reference = normalized_reference.removeprefix("http://t.me/")

    if normalized_reference.startswith("@"):
        normalized_reference = normalized_reference[1:]

    return normalized_reference.rstrip("/")


def _sanitize_attachment_name(file_name: str) -> str:
    """Очищает имя вложения от символов, неудобных для локального сохранения.

    :param file_name: Исходное имя файла.
    :returns: Безопасное имя файла в ASCII-совместимом виде.
    """

    cleaned_name = re.sub(r"[^\w.\-]+", "_", file_name.strip(), flags=re.ASCII)
    cleaned_name = cleaned_name.strip("._")
    return cleaned_name or "attachment"


def _build_message_link(chat_reference: str, message_id: int) -> str:
    """Строит публичную ссылку на сообщение.

    :param chat_reference: Нормализованное имя пользователя чата.
    :param message_id: Идентификатор сообщения.
    :returns: URL сообщения для публичного чата.
    """

    return f"https://t.me/{chat_reference}/{message_id}"


def _format_author_name(message: Message) -> str:
    """Формирует компактное имя автора сообщения.

    :param message: Сообщение Telegram.
    :returns: Имя пользователя, отображаемое в Markdown.
    """

    sender = getattr(message, "sender", None)
    if sender is None:
        return "Неизвестный автор"

    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"

    first_name = getattr(sender, "first_name", None) or ""
    last_name = getattr(sender, "last_name", None) or ""
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if full_name:
        return full_name

    title = getattr(sender, "title", None)
    if title:
        return title

    sender_id = getattr(sender, "id", None)
    if sender_id is not None:
        return f"ID {sender_id}"

    return "Неизвестный автор"


async def _download_message_attachment(
    client: TelegramClient,
    message: Message,
    attachments_dir: Path,
) -> Path | None:
    """Скачивает вложение сообщения в локальную подпапку.

    :param client: Авторизованный клиент Telethon.
    :param message: Сообщение Telegram с медиа-вложением.
    :param attachments_dir: Каталог для сохранения вложений.
    :returns: Путь к сохранённому файлу или ``None``, если вложения нет.
    """

    if message.media is None:
        return None

    file_name = None
    if message.file is not None and message.file.name:
        file_name = message.file.name
    elif message.file is not None and message.file.ext:
        file_name = f"message_{message.id}{message.file.ext}"
    else:
        file_name = f"message_{message.id}.bin"

    target_path = attachments_dir / _sanitize_attachment_name(file_name)
    downloaded_path = await client.download_media(message, file=target_path)
    if downloaded_path is None:
        return None

    return Path(downloaded_path)


def _build_markdown_header(
    document_name: str,
    export_date: datetime,
    chat_reference: str,
) -> str:
    """Создаёт обязательную шапку Markdown-документа.

    :param document_name: Полное имя документа.
    :param export_date: Дата экспорта.
    :param chat_reference: Имя пользователя Telegram-чата.
    :returns: Текст YAML-подобной шапки.
    """

    description = (
        "Экспорт истории сообщений Telegram-чата для последующего анализа вопросов "
        "и ответов по теме АГР."
    )
    lines = [
        "---",
        f"name: {document_name}",
        f"Date: {export_date.strftime('%Y-%m-%d')}",
        f"description: {description}",
        f"source: https://t.me/{chat_reference}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _build_message_markdown(
    message: Message,
    chat_reference: str,
    relative_attachment_path: Path | None,
    topic_title: str | None,
    topic_id: int | None,
) -> str:
    """Преобразует сообщение Telegram в Markdown-блок.

    :param message: Сообщение Telegram.
    :param chat_reference: Нормализованное имя пользователя чата.
    :param relative_attachment_path: Относительный путь к вложению, если оно есть.
    :param topic_title: Название топика, если сообщение относится к форумной теме.
    :param topic_id: Идентификатор топика, если он определён.
    :returns: Готовый Markdown-блок сообщения.
    """

    message_date = message.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    author_name = _format_author_name(message)
    message_link = _build_message_link(chat_reference=chat_reference, message_id=message.id)
    text = (message.text or "").strip()

    lines = [
        f"## Сообщение #{message.id}",
        f"- Дата: {message_date}",
        f"- Автор: {author_name}",
        f"- Ссылка: [{message_link}]({message_link})",
    ]

    if topic_title is not None:
        topic_line = topic_title
        if topic_id is not None:
            topic_line = f"{topic_title} (ID {topic_id})"
        lines.append(f"- Топик: {topic_line}")

    if message.reply_to_msg_id is not None:
        lines.append(f"- Ответ на: #{message.reply_to_msg_id}")

    lines.append("")

    if text:
        lines.append(text)
        lines.append("")
    else:
        lines.append("_Сообщение без текстового содержимого._")
        lines.append("")

    if relative_attachment_path is not None:
        relative_attachment = relative_attachment_path.as_posix()
        lines.append(f"Вложение: [{relative_attachment}]({relative_attachment})")
        lines.append("")

    return "\n".join(lines)


async def _load_forum_topics(client: TelegramClient, chat_entity: object) -> dict[int, str]:
    """Загружает все доступные топики форумного чата.

    :param client: Авторизованный клиент Telethon.
    :param chat_entity: Сущность Telegram-чата.
    :returns: Словарь ``topic_id -> title``.
    """

    topic_titles: dict[int, str] = {}
    offset_date = None
    offset_id = 0
    offset_topic = 0

    while True:
        result = await client(
            GetForumTopicsRequest(
                peer=chat_entity,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=100,
                q="",
            )
        )
        if not result.topics:
            break

        for topic in result.topics:
            topic_titles[topic.id] = topic.title

        if len(result.topics) < 100:
            break

        last_topic = result.topics[-1]
        offset_date = last_topic.date
        offset_id = last_topic.top_message
        offset_topic = last_topic.id

    return topic_titles


def _resolve_message_topic(
    message: Message,
    topic_titles: dict[int, str],
) -> tuple[int | None, str | None]:
    """Определяет топик, к которому относится сообщение.

    :param message: Сообщение Telegram.
    :param topic_titles: Словарь ``topic_id -> title``.
    :returns: Пара ``(topic_id, topic_title)``.
    """

    reply_to = getattr(message, "reply_to", None)
    reply_to_top_id = getattr(reply_to, "reply_to_top_id", None)
    if reply_to_top_id in topic_titles:
        return reply_to_top_id, topic_titles[reply_to_top_id]

    if message.reply_to_msg_id in topic_titles:
        topic_id = message.reply_to_msg_id
        return topic_id, topic_titles[topic_id]

    if message.id in topic_titles:
        return message.id, topic_titles[message.id]

    general_topic_id = 1
    if general_topic_id in topic_titles:
        return general_topic_id, topic_titles[general_topic_id]

    return None, None


async def export_telegram_chat_to_markdown(
    api_id: int,
    api_hash: str,
    phone: str,
    chat_reference: str,
    output_path: str | Path,
    session_path: str | Path = "telegram_session",
    limit: int | None = None,
) -> Path:
    """Экспортирует историю Telegram-чата в Markdown-файл с вложениями.

    :param api_id: Идентификатор Telegram API.
    :param api_hash: Хэш Telegram API.
    :param phone: Телефон для авторизации в Telegram.
    :param chat_reference: Ссылка на чат, ``@username`` или имя пользователя.
    :param output_path: Путь к выходному Markdown-файлу.
    :param session_path: Путь к файлу сессии Telethon.
    :param limit: Ограничение на количество сообщений. Если ``None``, выгружается
        вся доступная история.
    :returns: Путь к созданному Markdown-файлу.
    :raises ValueError: Если переданы некорректные аргументы.
    :raises RuntimeError: Если не удалось получить данные из Telegram.

    Example:
        ``await export_telegram_chat_to_markdown(12345, "hash", "+79990000000",
        "ifc_mos", "data/ifc_mos.md")``
    """

    if api_id <= 0:
        raise ValueError("api_id должен быть положительным целым числом.")
    if not api_hash.strip():
        raise ValueError("api_hash не должен быть пустым.")
    if not phone.strip():
        raise ValueError("phone не должен быть пустым.")

    normalized_chat_reference = _normalize_chat_reference(chat_reference=chat_reference)
    if not normalized_chat_reference:
        raise ValueError("chat_reference не должен быть пустым.")

    target_markdown_path = Path(output_path).resolve()
    if target_markdown_path.suffix.lower() != ".md":
        raise ValueError("output_path должен указывать на Markdown-файл с расширением .md.")

    target_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    attachments_dir = target_markdown_path.parent / f"{target_markdown_path.stem}_attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(Path(session_path).resolve()), api_id, api_hash)
    document_name = f"Экспорт Telegram-чата {normalized_chat_reference}"
    export_date = datetime.now()
    markdown_blocks: list[str] = [
        _build_markdown_header(
            document_name=document_name,
            export_date=export_date,
            chat_reference=normalized_chat_reference,
        ),
        f"# {document_name}",
        "",
        f"Источник: [https://t.me/{normalized_chat_reference}](https://t.me/{normalized_chat_reference})",
        f"Дата экспорта: {export_date.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    try:
        await client.start(phone=phone)
        chat_entity = await client.get_entity(normalized_chat_reference)
        topic_titles = await _load_forum_topics(client=client, chat_entity=chat_entity)

        async for message in client.iter_messages(chat_entity, limit=limit, reverse=True):
            if not isinstance(message, Message):
                continue
            if message.action is not None:
                continue

            topic_id, topic_title = _resolve_message_topic(
                message=message,
                topic_titles=topic_titles,
            )

            attachment_path = await _download_message_attachment(
                client=client,
                message=message,
                attachments_dir=attachments_dir,
            )
            relative_attachment_path = None
            if attachment_path is not None:
                relative_attachment_path = attachment_path.relative_to(target_markdown_path.parent)

            markdown_blocks.append(
                _build_message_markdown(
                    message=message,
                    chat_reference=normalized_chat_reference,
                    relative_attachment_path=relative_attachment_path,
                    topic_title=topic_title,
                    topic_id=topic_id,
                )
            )
            markdown_blocks.append("---")
            markdown_blocks.append("")

    except FloodWaitError as error:
        raise RuntimeError(
            f"Telegram временно ограничил запросы. Повторите позже через {error.seconds} секунд."
        ) from error
    except Exception as error:
        raise RuntimeError(f"Не удалось экспортировать историю чата: {error}") from error
    finally:
        await client.disconnect()

    target_markdown_path.write_text("\n".join(markdown_blocks).strip() + "\n", encoding="utf-8")
    return target_markdown_path


def _build_argument_parser() -> argparse.ArgumentParser:
    """Создаёт CLI-парсер для экспорта Telegram-чата.

    :returns: Настроенный объект ``ArgumentParser``.
    """

    parser = argparse.ArgumentParser(
        description="Экспортирует историю Telegram-чата в Markdown с вложениями."
    )
    parser.add_argument("--api-id", type=int, required=True, help="Идентификатор Telegram API.")
    parser.add_argument("--api-hash", required=True, help="Хэш Telegram API.")
    parser.add_argument("--phone", required=True, help="Телефон для авторизации в Telegram.")
    parser.add_argument(
        "--chat",
        required=True,
        help="Ссылка на чат, имя пользователя или значение вида @username.",
    )
    parser.add_argument("--output", required=True, help="Путь к выходному Markdown-файлу.")
    parser.add_argument(
        "--session",
        default="telegram_session",
        help="Путь к файлу сессии Telethon.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Максимальное количество сообщений для выгрузки.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Запускает CLI-экспорт Telegram-чата.

    :param argv: Необязательный список аргументов командной строки.
    :returns: Код завершения процесса.
    """

    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)

    asyncio.run(
        export_telegram_chat_to_markdown(
            api_id=arguments.api_id,
            api_hash=arguments.api_hash,
            phone=arguments.phone,
            chat_reference=arguments.chat,
            output_path=arguments.output,
            session_path=arguments.session,
            limit=arguments.limit,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
