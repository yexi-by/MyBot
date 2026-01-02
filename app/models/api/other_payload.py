from typing import Literal

from pydantic import BaseModel


class LoginInfo(BaseModel):
    action: Literal["get_login_info"] = "get_login_info"
    echo: str
