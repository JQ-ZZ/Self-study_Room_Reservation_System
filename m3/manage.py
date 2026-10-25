"""管理命令不会启动服务：迁移、显式创建账号、执行一轮后台任务。"""
import argparse
from getpass import getpass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from shared.config import Settings
from shared.db import database
from shared.models import User
from shared.security import new_user, PERMISSIONS


def migration_config(url):
    config = Config(str(Path(__file__).with_name('alembic.ini')))
    config.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
    return config


def migrate(url):
    engine, _ = database(url)
    engine.dispose()
    command.upgrade(migration_config(url), 'head')


def main():
    parser = argparse.ArgumentParser(description='M3 独立管理工具')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('migrate')
    sub.add_parser('tick')
    user = sub.add_parser('create-user')
    user.add_argument('--username', required=True)
    user.add_argument('--name', required=True)
    user.add_argument('--role', choices=PERMISSIONS, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == 'migrate':
        migrate(settings.database_url)
        print('M3 / 公共层迁移完成')
    elif args.command == 'create-user':
        from shared.routes import UserCreate
        password = getpass('请输入至少 12 位密码（不回显）：')
        if password != getpass('再次输入：'):
            parser.error('两次密码不一致')
        p = UserCreate(username=args.username, display_name=args.name, role=args.role, password=password)
        engine, sessions = database(settings.database_url)
        with sessions.begin() as db:
            if db.scalar(select(User.id).where(User.username == p.username)):
                parser.error('账号已存在，未覆盖密码')
            db.add(new_user(p.username, p.password, p.display_name, p.role))
        engine.dispose()
        print('账号创建成功')
    else:
        from m3.app import create_app
        from m3.worker import tick
        app = create_app(settings, background=False)
        try:
            tick(app)
        finally:
            app.state.m1.close()
            app.state.engine.dispose()
        print('后台任务已执行一轮')


if __name__ == '__main__':
    main()
