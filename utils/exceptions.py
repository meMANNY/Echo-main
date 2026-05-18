from enum import IntEnum
from typing import TypedDict

class ExceptionCodes(IntEnum):
    BAD_REQUEST = 0
    DISCONNECT = 1
    INVALID_HEADER = 2
    NOT_FOUND = 3
    USER_EXISTS = 4
    UNAUTHORIZED = 5
    INCOMPLETE = 6


class RequestExceptionDict(TypedDict):
    msg: str
    code: int

class RequestException(Exception):

    def __init__(self,msg: str, code: ExceptionCodes, *args: object)-> None:
        self.msg = msg
        self.code = code
        self.args = args
        super().__init__(msg)

    @classmethod
    def to_dict(cls, exception: "RequestException") -> RequestExceptionDict:

        
        return {
            "msg": exception.msg,
            "code": exception.code.value
        }
    
    @classmethod
    def from_dict(cls, data: RequestExceptionDict) -> "RequestException":

        return cls(
            msg=data["msg"],
            code=ExceptionCodes(data["code"])
        )


    

