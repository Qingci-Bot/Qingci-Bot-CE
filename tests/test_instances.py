"""实例管理（完全自包含目录）测试"""

import pytest

import bot.instances as m


@pytest.fixture
def instances_dir(tmp_path, monkeypatch):
    """将实例注册表重定向到临时目录"""
    monkeypatch.setattr(m, "instances_dir", lambda: tmp_path)
    return tmp_path


def test_is_valid_name():
    assert m.is_valid_name("bot-a_1")
    assert not m.is_valid_name("")
    assert not m.is_valid_name(".hidden")
    assert not m.is_valid_name("a/b")
    assert not m.is_valid_name("a b")


def test_create_and_list(instances_dir):
    inst = m.create_instance("alpha", description="测试实例")
    assert inst.name == "alpha"
    assert inst.port == m.DEFAULT_PORT
    assert inst.created_at

    # 目录结构完整（config.yaml 模板 + plugins/ + data/）
    d = instances_dir / "alpha"
    assert (d / "config.yaml").is_file()
    assert (d / "plugins").is_dir()
    assert (d / "data").is_dir()

    listed = m.list_instances()
    assert [i.name for i in listed] == ["alpha"]


def test_create_duplicate_raises(instances_dir):
    m.create_instance("alpha")
    with pytest.raises(ValueError):
        m.create_instance("alpha")


def test_create_invalid_name_raises(instances_dir):
    with pytest.raises(ValueError):
        m.create_instance("../escape")
    assert not (instances_dir / ".." / "escape").exists()


def test_port_auto_allocates_distinct(instances_dir):
    a = m.create_instance("a")
    b = m.create_instance("b")
    assert a.port != b.port


def test_get_and_delete(instances_dir):
    m.create_instance("alpha")
    inst = m.get_instance("alpha")
    assert inst is not None and inst.name == "alpha"
    assert m.get_instance("missing") is None

    assert m.delete_instance("alpha") is True
    assert m.get_instance("alpha") is None
    assert m.delete_instance("alpha") is False


def test_delete_invalid_name_returns_false(instances_dir):
    """非法实例名（路径穿越）必须拒绝删除，不得触碰实例目录之外"""
    assert m.delete_instance("..") is False
    assert m.delete_instance("../evil") is False
    assert m.delete_instance(".hidden") is False


def test_metadata_roundtrip_preserves_port(instances_dir):
    m.create_instance("alpha", port=9100)
    assert m.get_instance("alpha").port == 9100


def test_explicit_port_conflict_raises(instances_dir):
    """显式指定已被其他实例占用的端口必须拒绝（冲突 400 由 API 层转换）"""
    m.create_instance("alpha", port=9100)
    with pytest.raises(ValueError, match="端口"):
        m.create_instance("beta", port=9100)
    # 未创建的实例不落盘
    assert m.get_instance("beta") is None
    # 另一空闲端口不受影响
    inst = m.create_instance("beta", port=9101)
    assert inst.port == 9101


def test_default_name_prefers_default_instance(instances_dir):
    m.create_instance("aaa")
    m.create_instance("default")
    assert m.default_instance_name() == "default"


def test_default_name_falls_back_to_first_sorted(instances_dir):
    m.create_instance("zeta")
    m.create_instance("alpha")
    assert m.default_instance_name() == "alpha"


def test_ensure_creates_default_when_empty(instances_dir):
    inst = m.ensure_default_instance()
    assert inst.name == m.DEFAULT_INSTANCE_NAME
    assert m.get_instance("default") is not None
    # 再次调用不重复创建
    assert m.ensure_default_instance().name == "default"
    assert len(m.list_instances()) == 1


def test_ensure_returns_existing_default(instances_dir):
    m.create_instance("default")
    inst = m.ensure_default_instance()
    assert inst.name == "default"
    assert len(m.list_instances()) == 1


def test_rename_instance(instances_dir):
    m.create_instance("alpha")
    renamed = m.rename_instance("alpha", "beta")
    assert renamed.name == "beta"
    assert m.get_instance("alpha") is None
    assert m.get_instance("beta") is not None
    # 元数据 name 已更新
    assert (instances_dir / "beta" / "instance.json").is_file()


def test_rename_missing_instance_raises(instances_dir):
    with pytest.raises(ValueError):
        m.rename_instance("ghost", "new")


def test_rename_to_existing_raises(instances_dir):
    m.create_instance("alpha")
    m.create_instance("beta")
    with pytest.raises(ValueError):
        m.rename_instance("alpha", "beta")


def test_rename_invalid_name_raises(instances_dir):
    m.create_instance("alpha")
    with pytest.raises(ValueError):
        m.rename_instance("alpha", "a/b")
    assert m.get_instance("alpha") is not None
