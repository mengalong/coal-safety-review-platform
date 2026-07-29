from coal_platform.uat import active_standard_version


def test_active_standard_version_selects_only_published_catalog_content() -> None:
    standards = [
        {"versions": [{"id": "draft", "status": "draft"}]},
        {"versions": [{"id": "active", "status": "active"}]},
    ]
    assert active_standard_version(standards) == {"id": "active", "status": "active"}
    assert active_standard_version([{"versions": [{"id": "draft", "status": "draft"}]}]) is None
