from open_webui.utils.misc import (
    API_KEY_NON_DISCLOSURE_SYSTEM_PROMPT,
    add_api_key_non_disclosure_system_message,
)
from open_webui.utils.payload import apply_system_prompt_to_body


def test_api_key_non_disclosure_prompt_is_prepended_and_idempotent():
    messages = [{"role": "user", "content": "hello"}]

    add_api_key_non_disclosure_system_message(messages)
    add_api_key_non_disclosure_system_message(messages)

    assert messages[0]["role"] == "system"
    assert messages[0]["content"].count(API_KEY_NON_DISCLOSURE_SYSTEM_PROMPT) == 1
    assert messages[1]["role"] == "user"


def test_apply_system_prompt_keeps_api_key_non_disclosure_first():
    form_data = {"messages": [{"role": "user", "content": "hello"}]}

    apply_system_prompt_to_body("You are helpful.", form_data)

    system_content = form_data["messages"][0]["content"]
    assert system_content.startswith(API_KEY_NON_DISCLOSURE_SYSTEM_PROMPT)
    assert "You are helpful." in system_content
