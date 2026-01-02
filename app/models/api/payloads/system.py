"""系统相关 Payload 模型"""

from typing import Literal

from pydantic import BaseModel


# ==================== 获取版本信息 ====================
class GetVersionInfoPayload(BaseModel):
    """获取版本信息"""

    action: Literal["get_version_info"] = "get_version_info"
    echo: str


# ==================== 获取packet状态 ====================
class NcGetPacketStatusPayload(BaseModel):
    """获取packet状态"""

    action: Literal["nc_get_packet_status"] = "nc_get_packet_status"
    echo: str


# ==================== 获取机器人账号范围 ====================
class GetRobotUinRangePayload(BaseModel):
    """获取机器人账号范围"""

    action: Literal["get_robot_uin_range"] = "get_robot_uin_range"
    echo: str


# ==================== 账号退出 ====================
class BotExitPayload(BaseModel):
    """账号退出"""

    action: Literal["bot_exit"] = "bot_exit"


# ==================== 检查是否可以发送图片 ====================
class CanSendImagePayload(BaseModel):
    """检查是否可以发送图片"""

    action: Literal["can_send_image"] = "can_send_image"
    echo: str


# ==================== 检查是否可以发送语音 ====================
class CanSendRecordPayload(BaseModel):
    """检查是否可以发送语音"""

    action: Literal["can_send_record"] = "can_send_record"
    echo: str


# ==================== OCR 图片识别 ====================
class OcrImageParams(BaseModel):
    image: str


class OcrImagePayload(BaseModel):
    """OCR 图片识别"""

    action: Literal["ocr_image"] = "ocr_image"
    params: OcrImageParams
    echo: str


# ==================== 英译中 ====================
class TranslateEn2ZhParams(BaseModel):
    words: list[str]


class TranslateEn2ZhPayload(BaseModel):
    """英译中"""

    action: Literal["translate_en2zh"] = "translate_en2zh"
    params: TranslateEn2ZhParams
    echo: str


# ==================== 设置输入状态 ====================
class SetInputStatusParams(BaseModel):
    group_id: int | None = None
    user_id: int | None = None
    eventType: str


class SetInputStatusPayload(BaseModel):
    """设置输入状态"""

    action: Literal["set_input_status"] = "set_input_status"
    params: SetInputStatusParams


# ==================== 获取cookies ====================
class GetCookiesParams(BaseModel):
    domain: str


class GetCookiesPayload(BaseModel):
    """获取cookies"""

    action: Literal["get_cookies"] = "get_cookies"
    params: GetCookiesParams
    echo: str


# ==================== 获取 CSRF Token ====================
class GetCsrfTokenPayload(BaseModel):
    """获取 CSRF Token"""

    action: Literal["get_csrf_token"] = "get_csrf_token"
    echo: str


# ==================== 获取 QQ 相关接口凭证 ====================
class GetCredentialsParams(BaseModel):
    domain: str


class GetCredentialsPayload(BaseModel):
    """获取 QQ 相关接口凭证"""

    action: Literal["get_credentials"] = "get_credentials"
    params: GetCredentialsParams
    echo: str


# ==================== nc获取rkey ====================
class NcGetRkeyPayload(BaseModel):
    """nc获取rkey"""

    action: Literal["nc_get_rkey"] = "nc_get_rkey"
    echo: str


# ==================== 获取rkey ====================
class GetRkeyPayload(BaseModel):
    """获取rkey"""

    action: Literal["get_rkey"] = "get_rkey"
    echo: str


# ==================== 获取clientkey ====================
class GetClientkeyPayload(BaseModel):
    """获取clientkey"""

    action: Literal["get_clientkey"] = "get_clientkey"
    echo: str


# ==================== 获取AI语音 ====================
class GetAiRecordParams(BaseModel):
    character: str
    text: str
    group_id: int | None = None


class GetAiRecordPayload(BaseModel):
    """获取AI语音"""

    action: Literal["get_ai_record"] = "get_ai_record"
    params: GetAiRecordParams
    echo: str


# ==================== 检查链接安全性 ====================
class CheckUrlSafelyParams(BaseModel):
    url: str


class CheckUrlSafelyPayload(BaseModel):
    """检查链接安全性"""

    action: Literal["check_url_safely"] = "check_url_safely"
    params: CheckUrlSafelyParams
    echo: str


# ==================== 获取小程序卡片 ====================
class GetMiniAppArkParams(BaseModel):
    type: str
    title: str
    desc: str
    picUrl: str
    jumpUrl: str


class GetMiniAppArkPayload(BaseModel):
    """获取小程序卡片"""

    action: Literal["get_mini_app_ark"] = "get_mini_app_ark"
    params: GetMiniAppArkParams
    echo: str


# ==================== 获取收藏列表 ====================
class GetCollectionListParams(BaseModel):
    category: int
    count: int = 10


class GetCollectionListPayload(BaseModel):
    """获取收藏列表"""

    action: Literal["get_collection_list"] = "get_collection_list"
    params: GetCollectionListParams
    echo: str
