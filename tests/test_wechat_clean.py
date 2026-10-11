# Contact names and credentials below are fictional test fixtures.
import json
import sqlite3
import hashlib

import pytest

from aitown.wechat_clean import Redactor, clean_export, initials


def test_redaction_preserves_dialogue_but_removes_sensitive_values():
    cleaner = Redactor({"李四": "ls"})
    value = "李四，明天见！手机号 +86 138-0000-0000；身份证 110101199001010010；邮箱 sample@example.com；密码: DemoOnly_123!；验证码 123456；银行卡 6222000000000000"
    result, counts = cleaner.clean(value)
    assert "ls,明天见" in result
    assert all(secret not in result for secret in ("138-0000", "110101199001010010", "sample@example", "DemoOnly", "123456", "6222000000000000", "李四"))
    assert {"phone", "id_card", "email", "password", "verification_code"} <= counts.keys()
    assert cleaner.clean(result)[0] == result


def test_obfuscated_values_and_credentials():
    result, _ = Redactor().clean("电话１３８００００００００\n身份证 110101 1990 0101 001X\npassword=demo_only_123\n地址：示例省示例市示例路1号\nhttps://example.com/?token=private\nwxid_demo_test\n123456789")
    assert "13800000000" not in result
    assert "1990" not in result
    assert "demo_only" not in result
    assert "示例路" not in result
    assert "example.com" not in result
    assert "wxid_" not in result
    assert "123456789" not in result


def test_alias_initials():
    assert initials("李四") == "ls"


def test_complete_output_has_no_raw_blobs_or_media_and_static_pages(tmp_path):
    root = tmp_path / "raw"
    (root / "HTML").mkdir(parents=True)
    (root / "Databases").mkdir()
    (root / "HTML" / "example_Contacts.csv").write_text(
        "user_demo,李四,https://example.com/avatar,\n", encoding="utf-8")
    table = "Chat_example"
    rows = [
        {"account": "raw_owner", "conversation_table": table, "timestamp": 1700000000,
         "direction": "outgoing", "type": 1, "content": "联系李四，13800000000", "content_encoding": "utf-8", "server_id": 987654321},
        {"account": "raw_owner", "conversation_table": table, "timestamp": 1700000001,
         "direction": "incoming", "type": 1, "content": None, "content_encoding": "zstd", "compressed_content_base64": "DO_NOT_COPY_RAW_SECRET_BLOB"},
        {"account": "raw_owner", "conversation_table": table, "timestamp": 1700000002,
         "direction": "incoming", "type": 3, "content": '<msg><img src="private.jpg"/></msg>', "content_encoding": "utf-8"},
        {"account": "raw_owner", "conversation_table": table, "timestamp": 1700000003,
         "direction": "incoming", "type": 1, "content": '<script>alert("x")</script>', "content_encoding": "utf-8"},
    ]
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (root / "messages.jsonl").write_text(raw, encoding="utf-8")
    db = sqlite3.connect(root / "Databases" / "message_0.sqlite")
    db.execute(f"CREATE TABLE {table}(CreateTime INTEGER)")
    db.executemany(f"INSERT INTO {table} VALUES(?)", [(row["timestamp"],) for row in rows])
    db.commit()
    db.close()
    output = tmp_path / "clean"
    report = clean_export(root, output, tmp_path / "private" / "aliases.json")
    assert report["source_records"] == 4
    assert report["database_count_matches_jsonl"]
    assert report["database_latest_matches_jsonl"]
    assert report["residual_rule_hits"] == 0
    cleaned = (output / "messages.jsonl").read_text(encoding="utf-8")
    assert all(value not in cleaned for value in ("raw_owner", "server_id", "compressed_content_base64", "DO_NOT_COPY_RAW_SECRET_BLOB", "13800000000", "李四", "private.jpg"))
    page = (output / "HTML" / "c0001" / "page-0001.html").read_text(encoding="utf-8")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert (root / "messages.jsonl").read_text(encoding="utf-8") == raw
    with pytest.raises(ValueError, match="已有"):
        clean_export(root, output, tmp_path / "private" / "aliases.json")


def test_group_identity_and_cross_message_password(tmp_path):
    root = tmp_path / "raw"
    (root / "HTML").mkdir(parents=True)
    (root / "Databases").mkdir()
    contact_db = sqlite3.connect(root / "Databases" / "WCDB_Contact.sqlite")
    contact_db.execute("CREATE TABLE Friend(userName TEXT)")
    contact_db.execute("INSERT INTO Friend VALUES('12345@chatroom')")
    contact_db.commit()
    contact_db.close()
    table = "Chat_" + hashlib.md5(b"12345@chatroom", usedforsecurity=False).hexdigest()
    values = ["wxid_demo:\n密码是什么？", "wxid_other:\nStandalone_Secret123"]
    rows = [{"account": "demo", "conversation_table": table, "timestamp": 1700000000+i,
             "direction": "incoming", "type": 1, "content": value, "content_encoding": "utf-8"}
            for i, value in enumerate(values)]
    (root / "messages.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in rows), encoding="utf-8")
    output = tmp_path / "clean"
    report = clean_export(root, output, tmp_path / "private" / "aliases.json")
    items = [json.loads(line) for line in (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    assert items[0]["speaker_id"] == "p00001"
    assert items[1]["content"] == "密码"
    assert all(i["conversation_kind"] == "group" for i in items)
    assert report["eligible_group_texts"] == 2
    assert report["unmatched_conversations"] == 0
