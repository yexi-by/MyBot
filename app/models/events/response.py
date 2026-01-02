from pydantic import BaseModel

class IDData(BaseModel):
    message_id: int
    
class SelfData(BaseModel):
    user_id:int
    nickname:str
    
class Response(BaseModel):
    status: str 
    retcode: int 
    data: IDData|SelfData
    message: str
    echo:int
    wording: str

    

    