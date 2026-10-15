"""Public stable core entrypoint. Not a hardware executor."""
from .contract import API,ContractError
from .runtime import Core
__all__=['API','ContractError','Core']
