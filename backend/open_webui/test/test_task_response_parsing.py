from open_webui.utils.middleware import extract_task_response_text


def test_extract_task_response_text_from_chat_completions_shape():
    response = {
        "choices": [
            {
                "message": {
                    "content": '{"title":"🎨 PPT 方案设计"}',
                }
            }
        ]
    }

    assert extract_task_response_text(response) == '{"title":"🎨 PPT 方案设计"}'


def test_extract_task_response_text_from_responses_api_shape():
    response = {
        "id": "resp_123",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"title":"🎨 PPT 方案设计"}',
                    }
                ],
            }
        ],
        "done": True,
    }

    assert extract_task_response_text(response) == '{"title":"🎨 PPT 方案设计"}'
