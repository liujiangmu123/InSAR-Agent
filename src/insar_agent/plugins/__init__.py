"""声明式插件清单:只读 YAML 匹配,绝不 import/exec 用户代码。"""

from insar_agent.plugins.loader import (
    Plugin,
    PluginWarning,
    load_plugins,
    match_viewers,
    plugin_roots,
)

__all__ = [
    "Plugin",
    "PluginWarning",
    "load_plugins",
    "match_viewers",
    "plugin_roots",
]
