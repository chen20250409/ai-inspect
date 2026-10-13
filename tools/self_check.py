# -*- coding: utf-8 -*-
"""离线自检：直接调用 run_inference()，验证 6 张测试图的判定是否符合预期。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
os.environ.setdefault("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_tmp_upload"))
os.environ["DB_WAIT_RETRIES"] = "0"   # 离线自检，不需要连数据库

import main  # noqa: E402

EXPECT = {
    "normal01.jpg": "正常",
    "normal02.jpg": "正常",
    "defect01.jpg": "缺陷",
    "defect02.jpg": "缺陷",
    "defect03.jpg": "缺陷",
    "defect04.jpg": "缺陷",
}

base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata")
ok = True
print("%-14s %-6s %-8s %-10s %s" % ("文件", "判定", "置信度", "缺陷分", "期望/结果"))
print("-" * 72)
for name, want in EXPECT.items():
    with open(os.path.join(base, name), "rb") as f:
        r = main.run_inference(f.read())
    mark = "OK " if r["result"] == want else "FAIL"
    if r["result"] != want:
        ok = False
    print("%-14s %-6s %-8.4f %-10.4f %s -> %s  [%s]  %s"
          % (name, r["result"], r["confidence"], r["defect_score"],
             want, r["result"], mark, r["defect_type_cn"]))
print("-" * 72)
print("全部通过" if ok else "存在不符合预期的样本")
sys.exit(0 if ok else 1)
