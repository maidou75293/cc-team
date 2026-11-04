#!/usr/bin/env python3
# cards_dag.py — cc-team 唯一写入口 CLI（原 card.py，2026-10-30 MECH-STD1 工程标准化更名）。
# 名字即语义：本模块管的是**有状态 DAG**——卡=节点、deps=边、状态=节点色，
# 物化视图 state/dag.json 每个写命令自动刷新；协议头见 ccards/__init__.py（--help 会打印）。
# 用法: py -3.12 cards_dag.py <create|set|show|list|claim|start|bind|done|verdict|release|
#        note|cancel|thaw|push|next|dag|upstream|reindex|duty> ...
import os, sys
sys.dont_write_bytecode = True  # REQ-OSSPATH-1 配套：入口先关 pyc 写，防 ccards/__init__ 重编译
# 出的 pyc 嵌宿主绝对路径→路径纪律判据 grep 假红（-I 吞 PYTHON* 环境量，详注见 ccards/__init__.py）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ccards.cli import main

if __name__ == "__main__":
    main()
