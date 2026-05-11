from __future__ import annotations


class Strings:
    matrix_welcome: str
    telegram_welcome: str
    link_success_matrix: str
    link_success_telegram: str
    unlink_success_matrix: str
    unlink_success_telegram: str
    unlink_no_bridge_matrix: str
    unlink_no_bridge_telegram: str
    unlink_failed_matrix: str
    unlink_failed_telegram: str
    encrypted_room: str
    encrypted_room_link_denied: str
    already_linked_matrix: str
    already_linked_telegram: str
    code_not_found: str
    code_expired: str
    tg_chat_already_linked: str
    rate_limit_link: str
    rate_limit_code: str
    media_relay_failed: str
    attachment_label: str
    not_authorized: str


class EnglishStrings(Strings):
    def __init__(self, bot_username: str, matrix_user_id: str) -> None:
        self.matrix_welcome = (
            "Hello! To link this room with a Telegram chat:\n"
            f"1. Add the bot https://t.me/{bot_username} to the Telegram group.\n"
            "2. Make the bot an admin (or grant it read access to messages/media).\n"
            "3. You will receive a command like /tg link [CODE] — enter it here."
        )
        self.telegram_welcome_template = (
            "Hello! To connect the bridge, invite {matrix_user_id} to a Matrix room "
            "and send the following command there:\n\n"
            "/tg link {link_code}"
        )
        self.link_success_matrix = "Telegram chat successfully linked to this room. Messages will be relayed."
        self.link_success_telegram = "Matrix room successfully linked. Messages will be relayed."
        self.unlink_success_matrix = "Bridge with Telegram removed."
        self.unlink_success_telegram = "Bridge with Matrix removed."
        self.unlink_no_bridge_matrix = "No active bridge for this room."
        self.unlink_no_bridge_telegram = "No active bridge for this chat."
        self.unlink_failed_matrix = "Failed to remove bridge. Please try again."
        self.unlink_failed_telegram = "Failed to remove bridge. Please try again."
        self.encrypted_room = (
            "This room uses encryption (E2EE). The bridge only works in unencrypted rooms. "
            "Please create a new room without encryption."
        )
        self.encrypted_room_link_denied = (
            "This room has encryption enabled. The bridge only supports unencrypted rooms."
        )
        self.already_linked_matrix = "This Matrix room is already linked to a Telegram chat."
        self.already_linked_telegram = (
            "This chat is already linked to a Matrix room. "
            "Run /tg unlink first, then request a new code."
        )
        self.code_not_found = "Code not found or already used. Request a new code in Telegram."
        self.code_expired = "The code has expired. Request a new code in Telegram."
        self.tg_chat_already_linked = (
            "This Telegram chat is already linked to another Matrix room. "
            "Remove the old bridge first or use a different chat."
        )
        self.rate_limit_link = "Too many link attempts. Please wait a few minutes."
        self.rate_limit_code = "Too many code requests. Please try again later."
        self.media_relay_failed = "Failed to forward attachment to Matrix (size, format, or network issue)."
        self.attachment_label = "attachment"
        self.not_authorized = "Not authorized: moderator or admin rights required for this command."

    def telegram_welcome(self, matrix_user_id: str, link_code: str) -> str:
        return (
            f"Hello! To connect the bridge, invite {matrix_user_id} to a Matrix room "
            f"and send the following command there:\n\n"
            f"/tg link {link_code}"
        )


class RussianStrings(Strings):
    def __init__(self, bot_username: str, matrix_user_id: str) -> None:
        self.matrix_welcome = (
            "Здравствуйте! Чтобы связать этот чат с чатом Telegram:\n"
            f"1. Добавьте бота https://t.me/{bot_username} в группу Telegram.\n"
            "2. Сделайте бота администратором (или дайте права на чтение сообщений/медиа).\n"
            "3. Вы получите команду вида /tg link [КОД] — введите её здесь."
        )
        self.link_success_matrix = "Чат Telegram успешно связан с этой комнатой. Сообщения будут пересылаться."
        self.link_success_telegram = "Комната Matrix успешно связана. Сообщения будут пересылаться."
        self.unlink_success_matrix = "Связка с Telegram разорвана."
        self.unlink_success_telegram = "Связка с Matrix разорвана."
        self.unlink_no_bridge_matrix = "Для этой комнаты нет активной связки."
        self.unlink_no_bridge_telegram = "Для этого чата нет активной связки."
        self.unlink_failed_matrix = "Не удалось разорвать связку. Повторите попытку."
        self.unlink_failed_telegram = "Не удалось разорвать связку. Повторите попытку."
        self.encrypted_room = (
            "Эта комната использует шифрование (E2EE). Бридж работает только "
            "в незашифрованных комнатах. Создайте новую комнату без шифрования."
        )
        self.encrypted_room_link_denied = (
            "В этой комнате включено шифрование. Бридж поддерживает только незашифрованные комнаты."
        )
        self.already_linked_matrix = "Эта комната Matrix уже связана с чатом Telegram."
        self.already_linked_telegram = (
            "Этот чат уже связан с комнатой Matrix. "
            "Сначала выполните /tg unlink, затем запрашивайте новый код."
        )
        self.code_not_found = "Код не найден или уже использован. Запросите новый код в Telegram."
        self.code_expired = "Срок действия кода истёк. Запросите новый код в Telegram."
        self.tg_chat_already_linked = (
            "Этот чат Telegram уже связан с другой комнатой Matrix. "
            "Сначала удалите старую связь или используйте другой чат."
        )
        self.rate_limit_link = "Слишком много попыток связки. Подождите несколько минут."
        self.rate_limit_code = "Слишком частые запросы кодов связки. Попробуйте позже."
        self.media_relay_failed = "Не удалось переслать вложение в Matrix (размер, формат или сеть)."
        self.attachment_label = "вложение"
        self.not_authorized = "Нет прав: для этой команды требуются права модератора или администратора."

    def telegram_welcome(self, matrix_user_id: str, link_code: str) -> str:
        return (
            f"Здравствуйте! Чтобы подключить чат-мост, добавьте бота {matrix_user_id} "
            f"в комнату Matrix и в том же чате введите команду:\n\n"
            f"/tg link {link_code}"
        )


def make_strings(locale: str, bot_username: str, matrix_user_id: str) -> Strings:
    if locale.lower().startswith("ru"):
        return RussianStrings(bot_username, matrix_user_id)
    return EnglishStrings(bot_username, matrix_user_id)
