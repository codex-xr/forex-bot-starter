import time
import pytest
from bot.access_control import (
    ADMIN_CHAT_ID,
    is_admin,
    generate_key,
    redeem_key,
    is_user_authorized,
    revoke_user,
    unban_user,
    grant_user,
    list_users_report,
    list_keys_report,
    get_user_plan_report,
    _STORE,
)


@pytest.fixture(autouse=True)
def clear_store():
    _STORE["keys"].clear()
    _STORE["users"].clear()


class TestAdminAccess:
    def test_admin_is_recognized(self):
        assert is_admin(ADMIN_CHAT_ID) is True
        assert is_admin(str(ADMIN_CHAT_ID)) is True
        assert is_admin(12345) is False

    def test_admin_is_always_authorized(self):
        auth, reason = is_user_authorized(ADMIN_CHAT_ID)
        assert auth is True
        assert reason == "Admin"


class TestVisitorTracking:
    def test_new_visitor_is_automatically_recorded(self):
        user_info = {"id": 888999, "username": "crypto_fan", "first_name": "Fan"}
        auth, status = is_user_authorized(888999, user_info)
        assert auth is False
        assert status == "unregistered"

        # User is in store as pending visitor
        assert "888999" in _STORE["users"]
        assert _STORE["users"]["888999"]["username"] == "crypto_fan"
        assert _STORE["users"]["888999"]["status"] == "pending"

    def test_admin_can_revoke_visitor(self):
        user_info = {"id": 777666, "username": "bad_visitor", "first_name": "Bad"}
        is_user_authorized(777666, user_info)

        # Admin revokes the visitor
        ok, msg = revoke_user("@bad_visitor")
        assert ok is True
        assert "REVOKED" in msg

        auth, status = is_user_authorized(777666, user_info)
        assert auth is False
        assert status == "revoked"

        # Revoked user cannot redeem a key
        key, _ = generate_key("30d")
        ok_redeem, msg_redeem = redeem_key(777666, user_info, key)
        assert ok_redeem is False
        assert "Account Revoked" in msg_redeem


class TestKeyGenerationAndRedemption:
    def test_generate_and_redeem_30d(self):
        key, label = generate_key("30d", note="Test Key")
        assert key.startswith("VIP-")
        assert "30D" in key
        assert label == "30 Days"

        user_info = {"id": 111222, "username": "trader_joe", "first_name": "Joe"}
        ok, msg = redeem_key(111222, user_info, key)
        assert ok is True
        assert "Access Activated Successfully" in msg

        auth, status = is_user_authorized(111222)
        assert auth is True
        assert status == "active"

    def test_generate_and_redeem_lifetime(self):
        key, label = generate_key("lifetime")
        assert "LIFE" in key
        assert label == "Lifetime"

        user_info = {"id": 333444, "username": "vip_whale", "first_name": "Whale"}
        ok, msg = redeem_key(333444, user_info, key)
        assert ok is True
        assert "Lifetime" in msg

        auth, status = is_user_authorized(333444)
        assert auth is True
        assert status == "active"

    def test_cannot_redeem_key_twice(self):
        key, _ = generate_key("7d")
        user1 = {"id": 111, "username": "user1"}
        user2 = {"id": 222, "username": "user2"}

        ok1, _ = redeem_key(111, user1, key)
        assert ok1 is True

        ok2, msg2 = redeem_key(222, user2, key)
        assert ok2 is False
        assert "already been redeemed" in msg2

    def test_cannot_redeem_invalid_key(self):
        ok, msg = redeem_key(999, {}, "VIP-FAKEXX-30D")
        assert ok is False
        assert "Invalid Activation Key" in msg


class TestUserRevocationAndRestore:
    def test_revoke_and_unban_user(self):
        key, _ = generate_key("30d")
        user_info = {"id": 555666, "username": "bad_actor", "first_name": "Bad"}
        redeem_key(555666, user_info, key)

        auth_before, _ = is_user_authorized(555666)
        assert auth_before is True

        # Admin revokes user
        ok, msg = revoke_user("555666")
        assert ok is True
        assert "REVOKED" in msg

        auth_after, status = is_user_authorized(555666)
        assert auth_after is False
        assert status == "revoked"

        # Admin restores / unbans user
        ok_unban, msg_unban = unban_user("555666")
        assert ok_unban is True
        assert "RESTORED" in msg_unban

        # Since 30d hasn't expired yet, active status is restored
        auth_restored, status_restored = is_user_authorized(555666)
        assert auth_restored is True
        assert status_restored == "active"


class TestDirectGrant:
    def test_grant_user_access(self):
        ok, msg = grant_user("999888", "14d", "Special VIP Grant")
        assert ok is True
        assert "Access granted" in msg

        auth, status = is_user_authorized(999888)
        assert auth is True
        assert status == "active"

    def test_grant_with_angle_brackets(self):
        ok, msg = grant_user("<5371081300>", "7d")
        assert ok is True
        assert "5371081300" in msg
        assert "<5371081300>" not in msg
        auth, status = is_user_authorized(5371081300)
        assert auth is True
        assert status == "active"


class TestReports:
    def test_list_users_report(self):
        # Add 1 active VIP, 1 visitor
        key, _ = generate_key("30d")
        redeem_key(1234, {"username": "vip_user"}, key)
        is_user_authorized(5678, {"username": "pending_visitor"})

        report = list_users_report()
        assert "Total Tracked Accounts" in report
        assert "vip_user" in report
        assert "pending_visitor" in report

    def test_list_keys_report(self):
        generate_key("7d")
        generate_key("30d")
        report = list_keys_report()
        assert "Available (Unused):</b> <code>2</code>" in report
        assert "VIP-" in report

    def test_user_plan_report(self):
        key, _ = generate_key("30d")
        redeem_key(4321, {"username": "plan_tester"}, key)

        rep = get_user_plan_report(4321)
        assert "VIP Account Active" in rep
        assert "30 Days" in rep
