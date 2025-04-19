from django.conf import settings
from django.utils.translation import get_language
from deepl import DeepLClient

from functools import cache


def get_deepl_client():
    return DeepLClient(settings.DEEPL_AUTH_KEY)


@cache
def trans(
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None
) -> str:
    deepl_client = get_deepl_client()

    text_result = deepl_client.translate_text(
        text=text,
        source_lang=source_lang,
        target_lang=target_lang or get_language()
    )

    return text_result.text
