"""工作区产物预览(声明式分发,各格式独立模块 @register)。

纪律:只读工作区内文件;不编造栅格/表格数值;缺依赖诚实 unsupported。
绝不 import 用户插件目录里的 Python。
"""

from insar_agent.preview.dispatch import Preview, PreviewOpts, preview_file, register

__all__ = ["Preview", "PreviewOpts", "preview_file", "register"]
