from pydantic import BaseModel
from typing import Literal

class LoginInfo(BaseModel):
    action:Literal["get_login_info"]="get_login_info"
    echo:int