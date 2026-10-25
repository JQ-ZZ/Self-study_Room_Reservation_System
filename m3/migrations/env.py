from alembic import context
from sqlalchemy import engine_from_config, pool
from shared.db import Base
import shared.models  # noqa: F401
import m3.models  # noqa: F401

config = context.config


def include_object(obj, name, type_, reflected, compare_to):
    # 共库时仅管理成员 C 的命名空间，绝不删除 M1/M2 表。
    if type_ == 'table':
        return name.startswith(('c_', 'm3_'))
    return True


options = dict(target_metadata=Base.metadata, version_table='c_alembic_version',
               include_object=include_object, compare_type=True)
if context.is_offline_mode():
    context.configure(url=config.get_main_option('sqlalchemy.url'), literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(config.get_section(config.config_ini_section), prefix='sqlalchemy.', poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, **options)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
