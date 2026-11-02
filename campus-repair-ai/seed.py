# -*- coding: utf-8 -*-
"""生成演示用假数据

用法：
  python seed.py           # 空库时插入 15 条
  python seed.py --reset   # 清空后重建（演示前重置状态）
"""
import sys

import app as core
import demo_data


def seed(reset=False):
    core.init_db()
    conn = core.get_db()
    exist = conn.execute('SELECT COUNT(*) c FROM tickets').fetchone()['c']
    if exist and not reset:
        print('已有 %d 条工单，跳过（要重建请加 --reset）' % exist)
        conn.close()
        return
    n = demo_data.insert_demo(conn, core.triage_rule, core.match_kb, reset=reset)
    conn.close()
    print('已插入 %d 条演示工单' % n)


if __name__ == '__main__':
    seed(reset='--reset' in sys.argv)
