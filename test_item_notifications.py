import json
import os
import tempfile
import unittest
from unittest.mock import patch

from modules.item_notifications import ItemNotificationState, resolved_hint_matches


class ResolvedHintMatchingTests(unittest.TestCase):
    def test_exact_numeric_and_clear_typo_results_match_item_ids(self):
        names = {"Mask Fragment: Left": 99, "Mask Fragment: Dragon": 100, "Map": 1}
        for query in ("Mask Fragment: Left", "mask fragment: left", "Mask Fragement: Left", "99"):
            with self.subTest(query=query):
                self.assertTrue(resolved_hint_matches(query, 99, names))
                self.assertFalse(resolved_hint_matches(query, 100, names))
        self.assertFalse(resolved_hint_matches("999", 999, names))

    def test_ambiguous_or_unrelated_queries_do_not_watch(self):
        names = {"Sword A": 1, "Sword B": 2}
        for item_id in (1, 2):
            self.assertFalse(resolved_hint_matches("Sword", item_id, names))
            self.assertFalse(resolved_hint_matches("Completely different", item_id, names))
            self.assertFalse(resolved_hint_matches("", item_id, names))


class ItemNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "preferences.json")
        self.state = ItemNotificationState(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_require_explicit_consent_and_modes_are_independent(self):
        self.state.bind_registration(100, "Alice#1234", ["Slot A"])
        self.state.add_watch(100, "Slot A", "Progressive Sword")
        self.assertEqual(self.state.recipients("Slot A", "Progressive Sword", [100]), [])
        self.state.set_mode(100, "watched")
        self.assertEqual(self.state.recipients("Slot A", "Progressive Sword", [100]), [100])
        self.assertEqual(self.state.recipients("Slot A", "Map", [100]), [])
        self.state.set_mode(100, "all")
        self.assertEqual(self.state.recipients("Slot A", "Map", [100]), [100])
        self.state.set_mode(100, "off")
        self.assertEqual(self.state.recipients("Slot A", "Progressive Sword", [100]), [])

    def test_watch_persists_and_unwatch_removes_it(self):
        self.state.bind_registration(100, "Alice#1234", ["Slot A"])
        self.state.add_watch(100, "Slot A", "Progressive Sword")
        restarted = ItemNotificationState(self.path)
        self.assertEqual(restarted.watches(100), [{"slot": "Slot A", "item": "Progressive Sword"}])
        self.assertTrue(restarted.remove_watch(100, "slot a", "progressive sword"))
        self.assertEqual(ItemNotificationState(self.path).watches(100), [])

    def test_registered_identity_resolves_without_discord_member_cache(self):
        registrations = os.path.join(self.temp.name, "registrations")
        os.makedirs(registrations)
        with open(os.path.join(registrations, "Alice#1234.json"), "w", encoding="utf-8") as output:
            json.dump(["Slot A"], output)
        self.state.bind_registration(100, "Alice#1234", ["Slot A"])
        self.state.set_mode(100, "all")
        self.assertEqual(self.state.registered_user_ids("slot a", registrations), [100])
        self.assertEqual(self.state.recipients("Slot A", "Map", self.state.registered_user_ids("Slot A", registrations)), [100])

    def test_delivery_consumes_matching_watch_and_replay_does_not_repeat(self):
        self.state.bind_registration(100, "Alice#1234", ["Slot A"])
        self.state.add_watch(100, "Slot A", "Progressive Sword")
        self.state.set_mode(100, "watched")
        self.assertEqual(self.state.complete_delivery("event-1", "Slot A", "Progressive Sword", [100]), [100])
        self.assertEqual(self.state.watches(100), [])
        restarted = ItemNotificationState(self.path)
        self.assertEqual(restarted.complete_delivery("event-1", "Slot A", "Progressive Sword", [100]), [])

    def test_filtered_delivery_completes_watch_without_consent_or_ping(self):
        self.state.bind_registration(100, "Alice#1234", ["Slot A"])
        self.state.add_watch(100, "Slot A", "Progressive Sword")
        self.assertEqual(self.state.complete_delivery("filtered-event", "Slot A", "Progressive Sword", [100]), [])
        self.assertEqual(self.state.watches(100), [])

    def test_corrupt_state_fails_closed_without_overwrite(self):
        with open(self.path, "w", encoding="utf-8") as output:
            output.write('{"users":{"100":{"mode":"all","watches":"bad"}}}')
        state = ItemNotificationState(self.path)
        self.assertFalse(state.available)
        self.assertFalse(state.set_mode(100, "all"))
        with open(self.path, "r", encoding="utf-8") as source:
            self.assertIn('"watches":"bad"', source.read())

    def test_legacy_optional_user_fields_are_normalized_and_non_numeric_ids_rejected(self):
        with open(self.path, "w", encoding="utf-8") as output:
            output.write('{"users":{"100":{}},"delivered":[]}')
        state = ItemNotificationState(self.path)
        self.assertTrue(state.available)
        self.assertTrue(state.add_watch(100, "Slot A", "Map"))
        self.assertEqual(state.watches(100), [{"slot": "Slot A", "item": "Map"}])
        with open(self.path, "w", encoding="utf-8") as output:
            output.write('{"users":{"alice":{}},"delivered":[]}')
        self.assertFalse(ItemNotificationState(self.path).available)

    def test_failed_atomic_persistence_rolls_back_opt_in_and_watch(self):
        with patch("modules.item_notifications.os.replace", side_effect=OSError("busy")):
            self.assertFalse(self.state.set_mode(100, "all"))
            self.assertFalse(self.state.add_watch(100, "Slot A", "Map"))
        self.assertEqual(self.state.recipients("Slot A", "Map", [100]), [])
        self.assertEqual(self.state.watches(100), [])

    def test_directory_creation_and_tempfile_errors_roll_back_opt_in(self):
        for target, error in (("modules.item_notifications.os.makedirs", OSError("mkdir failed")),
                              ("modules.item_notifications.tempfile.mkstemp", OSError("temp failed"))):
            with patch(target, side_effect=error):
                self.assertFalse(self.state.set_mode(100, "all"))
            self.assertEqual(self.state.mode(100), "off")

    def test_unregister_removes_consent_watches_and_registration_binding(self):
        self.state.bind_registration(100, "Alice#1234")
        self.state.set_mode(100, "all")
        self.state.add_watch(100, "Slot A", "Map")
        self.assertTrue(self.state.unregister(100))
        self.assertEqual(self.state.recipients("Slot A", "Map", [100]), [])
        self.assertEqual(self.state.watches(100), [])
        self.assertNotIn("100", self.state.data["users"])

    def test_rebind_changes_legacy_registration_lookup_without_transferring_consent(self):
        registrations = os.path.join(self.temp.name, "registrations")
        os.makedirs(registrations)
        with open(os.path.join(registrations, "OldName.json"), "w", encoding="utf-8") as output:
            json.dump(["Slot A"], output)
        self.state.bind_registration(100, "OldName", ["Slot A"])
        self.state.set_mode(100, "all")
        with open(os.path.join(registrations, "OldName.json"), "w", encoding="utf-8") as output:
            json.dump(["Slot A", "Slot B"], output)
        self.assertEqual(self.state.registered_user_ids("Slot A", registrations), [100])
        self.assertEqual(self.state.registered_user_ids("Slot B", registrations), [])
        self.state.bind_registration(100, "NewName")
        self.assertEqual(self.state.registered_user_ids("Slot A", registrations), [])
        self.assertEqual(self.state.recipients("Slot A", "Map", [100]), [100])


if __name__ == "__main__":
    unittest.main()
