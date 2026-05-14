"""
Compatibility module for config imports.

Canonical config types/functions currently live in tanishi.core.__init__.
"""

from tanishi.core import TanishiConfig, get_config

__all__ = ["TanishiConfig", "get_config"]
