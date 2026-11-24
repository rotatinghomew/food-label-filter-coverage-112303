"""项目根入口，支持 `python3 -I run_weekly.py data ...` 的隔离运行方式。

隔离模式（-I）不把当前目录加入 sys.path，这里显式补回脚本所在目录；
解释器仍不会从数据目录加载任何代码，数据路径只作为参数传入。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from weekly.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
