"""数据根目录（多实例隔离）与单实例互斥名派生测试"""

import pytest

from bot.paths import app_root, data_root, set_data_root


@pytest.fixture(autouse=True)
def _reset_data_root():
    """每个测试前重置数据根，避免污染其它测试"""
    from bot.paths import _data_root

    saved = _data_root
    set_data_root(app_root() / "data")
    yield
    set_data_root(saved or app_root() / "data")


def test_data_root_default_is_app_root_data():
    assert data_root() == (app_root() / "data").resolve()


def test_set_data_root_overrides(tmp_path):
    set_data_root(tmp_path)
    assert data_root() == tmp_path.resolve()


def test_db_path_follows_data_root(tmp_path, monkeypatch):
    import bot.db.engine as engine

    set_data_root(tmp_path)
    assert engine.db_path() == tmp_path.resolve() / "qingci-bot.db"


def test_mutex_name_derivation_stable_and_distinct(tmp_path):
    from desktop.py.single_instance import mutex_name_for_data_dir

    a = mutex_name_for_data_dir(tmp_path / "botA")
    b = mutex_name_for_data_dir(tmp_path / "botB")
    assert a != b
    # 同一路径派生结果稳定
    assert mutex_name_for_data_dir(tmp_path / "botA") == a
    # 名称符合 Windows 命名互斥量长度限制
    assert len(a) <= 260
