import time
import pytest
from unittest.mock import patch
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
    parse_schedule_timing,
    create_scheduled_broadcast,
    cancel_schedule,
    list_schedules_report,
    process_due_broadcasts,
    _STORE,
)


@pytest.fixture(autouse=True)
def clear_store():
    _STORE["keys"].clear()
    _STORE["users"].clear()
    _STORE["schedules"].clear()


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

        assert "888999" in _STORE["users"]
        assert _STORE["users"]["888999"]["username"] == "crypto_fan"
        assert _STORE["users"]["888999"]["status"] == "pending"

    def test_admin_can_revoke_visitor(self):
        user_info = {"id": 777666, "username": "bad_visitor", "first_name": "Bad"}
        is_user_authorized(777666, user_info)

        ok, msg = revoke_user("@bad_visitor")
        assert ok is True
        assert "REVOKED" in msg

        auth, status = is_user_authorized(777666, user_info)
        assert auth is False
        assert status == "revoked"

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

        ok, msg = revoke_user("555666")
        assert ok is True
        assert "REVOKED" in msg

        auth_after, status = is_user_authorized(555666)
        assert auth_after is False
        assert status == "revoked"

        ok_unban, msg_unban = unban_user("555666")
        assert ok_unban is True
        assert "RESTORED" in msg_unban

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


class TestScheduledBroadcasts:
    def test_parse_timing_expressions(self):
        # One-time in 2h
        ts, interval, label, is_rec = parse_schedule_timing("in 2h")
        assert is_rec is False
        assert interval is None
        assert "2 Hours" in label

        # Recurring every 24h
        ts_rec, interval_rec, label_rec, is_rec2 = parse_schedule_timing("every 24h")
        assert is_rec2 is True
        assert interval_rec == 86400
        assert "24 Hours" in label_rec

        # Daily at 08:30 UTC
        ts_daily, interval_daily, label_daily, is_rec3 = parse_schedule_timing("daily 08:30")
        assert is_rec3 is True
        assert interval_daily == 86400
        assert "08:30 UTC" in label_daily

    def test_create_and_cancel_schedule(self):
        ok, msg = create_scheduled_broadcast("in 2h", "London open is starting!", target="all")
        assert ok is True
        assert "One-Time Schedule Created" in msg
        assert "SB-" in msg

        # Extract ID from store
        sched_id = list(_STORE["schedules"].keys())[0]
        assert "schedules" in _STORE
        assert sched_id in _STORE["schedules"]

        # Cancel schedule
        ok_cancel, msg_cancel = cancel_schedule(sched_id)
        assert ok_cancel is True
        assert "CANCELLED" in msg_cancel
        assert sched_id not in _STORE["schedules"]

    @patch("bot.telegram.send_telegram_message")
    def test_process_due_broadcasts_one_time(self, mock_send):
        # Create schedule due in the past
        now = time.time()
        _STORE["schedules"]["SB-TEST1"] = {
            "id": "SB-TEST1",
            "message": "Immediate alert",
            "target": "all",
            "is_recurring": False,
            "interval_seconds": None,
            "timing_label": "Once",
            "next_run_ts": now - 10,
            "status": "active",
            "executions_count": 0,
        }

        executed = process_due_broadcasts()
        assert len(executed) == 1
        assert executed[0]["id"] == "SB-TEST1"
        assert mock_send.called
        # One-time schedule is completed and deleted
        assert "SB-TEST1" not in _STORE["schedules"]

    @patch("bot.telegram.send_telegram_message")
    def test_process_due_broadcasts_recurring(self, mock_send):
        now = time.time()
        _STORE["schedules"]["SB-REC1"] = {
            "id": "SB-REC1",
            "message": "Daily alert",
            "target": "all",
            "is_recurring": True,
            "interval_seconds": 86400,
            "timing_label": "Recurring 24h",
            "next_run_ts": now - 10,
            "status": "active",
            "executions_count": 0,
        }

        executed = process_due_broadcasts()
        assert len(executed) == 1
        assert executed[0]["id"] == "SB-REC1"
        assert mock_send.called

        # Recurring schedule is NOT deleted, its next_run_ts is advanced by 24h
        assert "SB-REC1" in _STORE["schedules"]
        assert _STORE["schedules"]["SB-REC1"]["next_run_ts"] > now
        assert _STORE["schedules"]["SB-REC1"]["executions_count"] == 1


class TestReports:
    def test_list_users_report(self):
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

    def test_list_schedules_report(self):
        create_scheduled_broadcast("every 12h", "Check BTC signals")
        rep = list_schedules_report()
        assert "Scheduled Broadcasts Dashboard" in rep
        assert "Check BTC signals" in rep
        assert "SB-" in rep

    def test_user_plan_report(self):
        key, _ = generate_key("30d")
        redeem_key(4321, {"username": "plan_tester"}, key)

        rep = get_user_plan_report(4321)
        assert "VIP Account Active" in rep
        assert "30 Days" in rep
