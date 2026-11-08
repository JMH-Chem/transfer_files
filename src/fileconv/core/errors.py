"""사용자에게 그대로 보여줄 수 있는 오류들."""


class ConvertError(Exception):
    """변환 중 발생한, 사용자에게 보여줄 메시지가 담긴 오류."""


class OptionError(ConvertError):
    """옵션 값이 잘못됨."""


class NoConverterError(ConvertError):
    """해당 형식 조합을 처리하는 변환기가 없음."""


class UnavailableError(ConvertError):
    """필요한 외부 프로그램/라이브러리가 없음."""


class Cancelled(ConvertError):
    """사용자가 취소함."""
