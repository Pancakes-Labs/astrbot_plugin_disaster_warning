"""EQSC 业务服务包。"""

from .eqsc_tsunami_poll_service import EqscTsunamiPollService
from .eqsc_typhoon_poll_service import EqscTyphoonPollService

__all__ = ["EqscTsunamiPollService", "EqscTyphoonPollService"]
