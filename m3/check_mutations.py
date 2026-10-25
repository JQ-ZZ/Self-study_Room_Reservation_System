"""可重复的故障注入验收；只改变本进程的函数，不编辑源码文件。"""
import argparse
import pytest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mutation', choices=['skip-release', 'bypass-ownership'])
    args = parser.parse_args()
    if args.mutation == 'skip-release':
        from m3.m1_client import M1Client
        M1Client.release = lambda self, ticket: None
        test = 'tests_m3/test_workflow.py::test_full_flow_restores_room_and_reviews_once'
    else:
        import m3.service
        m3.service.can_view = lambda ticket, user: True
        test = 'tests_m3/test_security.py::test_ticket_visibility_and_assignee_ownership'
    result = pytest.main([test, '-q', '--tb=no'])
    if result == pytest.ExitCode.TESTS_FAILED:
        print('故障注入被测试捕获：' + args.mutation)
        return 0
    print('故障注入未按预期捕获或测试运行异常：' + args.mutation)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
