from pathlib import Path
from modules.notion.mapper import (
    clean_notion_title,
    extract_domain,
    map_goods_payload,
    map_services_payload,
    NotionContentMapper,
)


def test_clean_notion_title():
    assert clean_notion_title(None) == "Untitled"
    assert clean_notion_title("") == "Untitled"
    assert clean_notion_title("   \n\t  ") == "Untitled"
    assert clean_notion_title("Simple Title") == "Simple Title"

    long_title = "A" * 150
    cleaned = clean_notion_title(long_title, max_len=50)
    assert len(cleaned) <= 53
    assert cleaned.endswith("...")


def test_extract_domain():
    assert extract_domain("https://example.com/test?a=1") == "example.com"
    assert extract_domain("http://sub.domain.org/path") == "sub.domain.org"
    assert extract_domain("") == ""


def test_map_goods_payload_lightweight():
    item = {
        "url": "https://shop.example.com/item/123",
        "title": "Minimal Sneaker",
    }
    payload = map_goods_payload(item)

    assert payload["Name"]["title"][0]["text"]["content"] == "Minimal Sneaker"
    assert payload["Url 1"]["url"] == "https://shop.example.com/item/123"
    assert "Categories" not in payload
    assert "Price" not in payload


def test_map_goods_payload_rich(tmp_path):
    md_file = tmp_path / "post.md"
    md_file.write_text("# Product Heading\nHigh quality winter boot.")

    item = {
        "url": "https://snow.example.com/boot",
        "title": "Pro Snowboard Boot",
        "price": 249.99,
        "markdown_path": str(md_file),
    }
    payload = map_goods_payload(
        item,
        category_notion_id="cat-snow-id",
        type_notion_id="type-boot-id",
        tag_notion_ids=["tag-winter", "tag-alpine"],
    )

    assert payload["Name"]["title"][0]["text"]["content"] == "Pro Snowboard Boot"
    assert payload["Url 1"]["url"] == "https://snow.example.com/boot"
    assert payload["Price"]["number"] == 249.99
    assert payload["Categories"]["relation"] == [{"id": "cat-snow-id"}]
    assert payload["Type"]["relation"] == [{"id": "type-boot-id"}]
    assert payload["Tags"]["relation"] == [{"id": "tag-winter"}, {"id": "tag-alpine"}]
    assert "High quality winter boot." in payload["Description"]["rich_text"][0]["text"]["content"]


def test_map_services_payload():
    item = {
        "url": "https://agency.design/contact",
        "title": "Creative Design Studio",
        "description": "Full-stack UI/UX and branding agency.",
    }
    payload = map_services_payload(
        item,
        category_notion_id="role-design-id",
        type_notion_id="type-studio-id",
        tag_notion_ids=["tag-creative"],
    )

    assert payload["Name"]["title"][0]["text"]["content"] == "Creative Design Studio"
    assert payload["URL"]["url"] == "https://agency.design/contact"
    assert payload["Domains"]["rich_text"][0]["text"]["content"] == "agency.design"
    assert payload["Roles"]["relation"] == [{"id": "role-design-id"}]
    assert payload["Types"]["relation"] == [{"id": "type-studio-id"}]
    assert payload["Tags"]["relation"] == [{"id": "tag-creative"}]
    assert payload["Description"]["rich_text"][0]["text"]["content"] == "Full-stack UI/UX and branding agency."


def test_notion_content_mapper_wrapper():
    item = {"url": "https://test.com", "title": "Test"}
    payload_goods = NotionContentMapper.map_goods_payload(item)
    payload_services = NotionContentMapper.map_services_payload(item)

    assert payload_goods["Name"]["title"][0]["text"]["content"] == "Test"
    assert payload_services["Name"]["title"][0]["text"]["content"] == "Test"
