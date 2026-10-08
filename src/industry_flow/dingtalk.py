from __future__ import annotations

import json
from pathlib import Path

import requests


DINGTALK_TOKEN_API = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
DINGTALK_MEDIA_UPLOAD_API = "https://oapi.dingtalk.com/media/upload"
DINGTALK_GROUP_SEND_API = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"


def send_text(webhook: str, message: str, timeout: int = 10) -> str:
    if not webhook:
        raise RuntimeError("DINGTALK_WEBHOOK is empty")

    payload = {
        "msgtype": "text",
        "text": {"content": message},
    }

    return _send_webhook_payload(webhook, payload, timeout)


def send_markdown(
    webhook: str,
    title: str,
    message: str,
    timeout: int = 30,
) -> str:
    if not webhook:
        raise RuntimeError("DINGTALK_WEBHOOK is empty")

    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": message},
    }
    return _send_webhook_payload(webhook, payload, timeout)


def _send_webhook_payload(webhook: str, payload: dict, timeout: int) -> str:
    try:
        response = requests.post(
            webhook,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException:
        # Webhook URLs carry posting credentials in their query string; don't
        # let Requests include that URL in an exception or traceback.
        raise RuntimeError(
            "DingTalk webhook request failed; check connectivity and webhook configuration"
        ) from None

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("DingTalk webhook returned invalid JSON") from exc
    if data.get("errcode", 0) != 0:
        raise RuntimeError(
            f"DingTalk webhook error: errcode={data.get('errcode')}, "
            f"errmsg={data.get('errmsg', 'unknown error')}"
        )
    return response.text


def _response_json(response: requests.Response, operation: str) -> dict:
    if not response.ok:
        try:
            error_data = response.json()
        except ValueError:
            error_data = {}
        error_code = error_data.get("errcode", error_data.get("code"))
        error_message = error_data.get("errmsg", error_data.get("message"))
        details = f", code={error_code}, message={error_message}" if error_code or error_message else ""
        raise RuntimeError(
            f"DingTalk {operation} failed with HTTP {response.status_code}{details}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"DingTalk {operation} returned invalid JSON") from exc
    errcode = data.get("errcode", 0)
    if errcode not in (0, "0", None):
        raise RuntimeError(
            f"DingTalk {operation} failed: errcode={errcode}, "
            f"errmsg={data.get('errmsg', 'unknown error')}"
        )
    return data


def get_app_access_token(client_id: str, client_secret: str, timeout: int = 20) -> str:
    if not client_id or not client_secret:
        raise RuntimeError("DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET are required")
    response = requests.post(
        DINGTALK_TOKEN_API,
        json={"appKey": client_id, "appSecret": client_secret},
        timeout=timeout,
    )
    data = _response_json(response, "access token request")
    token = data.get("accessToken")
    if not token:
        raise RuntimeError("DingTalk access token response did not contain accessToken")
    return str(token)


def upload_image(access_token: str, image_path: Path, timeout: int = 60) -> str:
    if not image_path.is_file():
        raise FileNotFoundError(f"DingTalk image does not exist: {image_path}")
    try:
        with image_path.open("rb") as image_file:
            response = requests.post(
                DINGTALK_MEDIA_UPLOAD_API,
                params={"access_token": access_token, "type": "image"},
                files={"media": (image_path.name, image_file, "image/png")},
                timeout=timeout,
            )
    except requests.RequestException:
        # The media API places access_token in its query string; suppress the
        # original exception so request URLs can never expose that token in logs.
        raise RuntimeError("DingTalk media upload request failed; check connectivity") from None
    data = _response_json(response, "media upload")
    media_id = data.get("media_id")
    if not media_id:
        raise RuntimeError("DingTalk media upload response did not contain media_id")
    return str(media_id)


def send_group_markdown(
    access_token: str,
    robot_code: str,
    open_conversation_id: str,
    title: str,
    message: str,
    timeout: int = 30,
) -> str:
    if not robot_code or not open_conversation_id:
        raise RuntimeError("DINGTALK_ROBOT_CODE and DINGTALK_OPEN_CONVERSATION_ID are required")
    payload = {
        "robotCode": robot_code,
        "openConversationId": open_conversation_id,
        "msgKey": "sampleMarkdown",
        "msgParam": json.dumps(
            {"title": title, "text": message}, ensure_ascii=False
        ),
    }
    response = requests.post(
        DINGTALK_GROUP_SEND_API,
        json=payload,
        headers={"x-acs-dingtalk-access-token": access_token},
        timeout=timeout,
    )
    data = _response_json(response, "group message send")
    return json.dumps(data, ensure_ascii=False)


def send_group_image_markdown(
    client_id: str,
    client_secret: str,
    robot_code: str,
    open_conversation_id: str,
    image_path: Path,
    title: str,
    timeout: int = 30,
) -> str:
    access_token = get_app_access_token(client_id, client_secret)
    media_id = upload_image(access_token, image_path)
    message = f"![{title}]({media_id})"
    return send_group_markdown(
        access_token,
        robot_code or client_id,
        open_conversation_id,
        title,
        message,
        timeout=timeout,
    )
